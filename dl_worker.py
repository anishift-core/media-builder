#!/usr/bin/env python3
"""
AniShift - GitHub Actions High-Speed MP4 Builder (cloud_worker/dl_worker.py)
Downloads HLS segments in parallel (24 workers), remuxes via FFmpeg into a faststart MP4,
and uploads to a GitHub Release for full-speed CDN download.
"""

import os
import sys
import re
import json
import time
import base64
import shutil
import argparse
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, quote
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

MEGAPLAY_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Referer': 'https://megaplay.buzz/',
    'Origin': 'https://megaplay.buzz'
}

WORKER_POOL = [
    "https://anishift-stream.anishift-sever.workers.dev",
    "https://anishift-stream-2.anishiftsever-svr.workers.dev",
    "https://anishift-stream-3.anishift-sever-3.workers.dev",
    "https://anishift-stream-4.anishift-sever-4.workers.dev"
]


def decrypt_megaplay_enc(enc_str):
    key_bytes = bytes.fromhex("693f4c4d5441783051362c3a7d35305500000000000000000000000000000000")
    iv_bytes = bytes.fromhex("57303b3237546f6155706c5f50252763")
    b64 = enc_str.replace('_', '/').replace('-', '+')
    pad = len(b64) % 4
    if pad != 0:
        b64 += '=' * (4 - pad)
    cipher_bytes = base64.b64decode(b64)
    decryptor = Cipher(algorithms.AES(key_bytes), modes.CBC(iv_bytes), backend=default_backend()).decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    pad_len = padded[-1]
    decrypted = padded[:-pad_len] if 1 <= pad_len <= 16 else padded
    return json.loads(decrypted.decode('utf-8', errors='ignore'))


def resolve_master_m3u8(anime_id, ep_num, title):
    session = requests.Session()
    session.headers.update(MEGAPLAY_HEADERS)
    for url in [
        f"https://megaplay.buzz/stream/ani/{anime_id}/{ep_num}/sub",
        f"https://megaplay.buzz/stream/mal/{anime_id}/{ep_num}/sub",
    ]:
        try:
            r = session.get(url, timeout=10)
            if r.status_code == 200:
                m = re.search(r'data-id=["\'](\d+)["\']', r.text)
                if m:
                    hdrs = {**MEGAPLAY_HEADERS, "X-Requested-With": "XMLHttpRequest"}
                    sr = session.get(f"https://megaplay.buzz/stream/getSources?id={m.group(1)}", headers=hdrs, timeout=10)
                    if sr.status_code == 200 and sr.json().get("enc"):
                        dec = decrypt_megaplay_enc(sr.json()["enc"])
                        if dec.get("file"):
                            return dec["file"]
        except Exception:
            pass

    for w in WORKER_POOL:
        try:
            r = requests.get(f"{w}/resolve-mega?id={quote(str(anime_id))}&ep={quote(str(ep_num))}&title={quote(str(title))}", timeout=12)
            if r.status_code == 200:
                d = r.json()
                if d.get("raw_hls") or d.get("hls"):
                    return d.get("raw_hls") or d.get("hls")
        except Exception:
            pass
    raise RuntimeError("Failed to resolve master.m3u8")


def get_segments(child_url):
    res = requests.get(child_url, headers=MEGAPLAY_HEADERS, timeout=12)
    res.raise_for_status()
    return [
        (line.strip() if line.strip().startswith("http") else urljoin(child_url, line.strip()))
        for line in res.text.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]


def build_mp4(segments, out_mp4_path):
    tmp_dir = tempfile.mkdtemp(prefix="as_dl_")
    tl = threading.local()

    def get_sess(reset=False):
        if reset or not hasattr(tl, "s"):
            s = requests.Session()
            s.headers.update(MEGAPLAY_HEADERS)
            adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            tl.s = s
        return tl.s

    def fetch_seg(idx, url, attempts=5):
        p = os.path.join(tmp_dir, f"seg_{idx:05d}.ts")
        if os.path.isfile(p) and os.path.getsize(p) > 512:
            return
        for a in range(attempts):
            try:
                r = get_sess(reset=(a >= 2)).get(url, timeout=(5, 10))
                if r.status_code == 200 and len(r.content) > 512:
                    with open(p, "wb") as f:
                        f.write(r.content)
                    return
            except Exception:
                pass
            time.sleep(0.15 * (a + 1))

    with ThreadPoolExecutor(max_workers=24) as pool:
        futs = [pool.submit(fetch_seg, i, u) for i, u in enumerate(segments)]
        for fut in as_completed(futs):
            fut.result()

    merged_ts = os.path.join(tmp_dir, "merged.ts")
    with open(merged_ts, "wb") as out_f:
        for i in range(len(segments)):
            p = os.path.join(tmp_dir, f"seg_{i:05d}.ts")
            if os.path.isfile(p):
                with open(p, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f, length=524288)

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-fflags", "+genpts+igndts",
        "-i", merged_ts,
        "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart",
        out_mp4_path
    ], check=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def upload_to_release(repo, tag, file_path, asset_name, token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    rel_res = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", headers=headers, timeout=15)
    if rel_res.status_code == 404:
        rel_res = requests.post(
            f"https://api.github.com/repos/{repo}/releases",
            headers=headers,
            json={"tag_name": tag, "name": "AniShift Download Cache", "prerelease": False},
            timeout=15
        )
    rel_res.raise_for_status()
    rel = rel_res.json()

    for asset in rel.get("assets", []):
        if asset.get("name") == asset_name:
            requests.delete(f"https://api.github.com/repos/{repo}/releases/assets/{asset['id']}", headers=headers, timeout=15)

    upload_url = rel["upload_url"].split("{")[0] + f"?name={quote(asset_name)}"
    with open(file_path, "rb") as f:
        up_res = requests.post(
            upload_url,
            headers={**headers, "Content-Type": "video/mp4"},
            data=f,
            timeout=300
        )
    up_res.raise_for_status()
    print(f"Uploaded: {up_res.json().get('browser_download_url')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anime-id", required=True)
    parser.add_argument("--ep", default="1")
    parser.add_argument("--quality", default="1080p")
    parser.add_argument("--title", default="Anime")
    parser.add_argument("--movie", default="0")
    parser.add_argument("--child-url", default="")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", default="dl-cache")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")

    asset_name = f"anishift_{args.anime_id}_ep{args.ep}_{args.quality}.mp4"
    child_url = args.child_url.strip()
    if not child_url:
        master = resolve_master_m3u8(args.anime_id, args.ep, args.title)
        res = requests.get(master, headers=MEGAPLAY_HEADERS, timeout=12)
        lines = res.text.split("\n")
        qualities = {}
        for i, l in enumerate(lines):
            if "RESOLUTION=" in l:
                m = re.search(r"RESOLUTION=(\d+)x(\d+)", l)
                lbl = f"{m.group(2)}p" if m else "720p"
                for j in range(i + 1, len(lines)):
                    nl = lines[j].strip()
                    if nl and not nl.startswith("#"):
                        qualities[lbl] = nl if nl.startswith("http") else urljoin(master, nl)
                        break
        child_url = qualities.get(args.quality) or list(qualities.values())[0]

    segments = get_segments(child_url)
    out_mp4 = os.path.join(tempfile.gettempdir(), asset_name)
    build_mp4(segments, out_mp4)
    upload_to_release(args.repo, args.tag, out_mp4, asset_name, token)


if __name__ == "__main__":
    main()

