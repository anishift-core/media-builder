# ⚡ AniShift GitHub Actions Video Download Worker

මෙම Cloud Worker එක මගින් **VPS එකක් නැතුව** GitHub Actions (Parallel Runners 20ක් දක්වා) පාවිච්චි කරලා MegaPlay HLS streams 24-thread parallel download කරලා `ffmpeg` වලින් MP4 හදලා GitHub Releases CDN එකට upload කරයි.

---

## 🚀 Setup කරන ආකාරය (Step-by-Step)

1. **GitHub Repository එකක් හදන්න:**
   - GitHub එකේ අලුත් **Public** repository එකක් හදන්න (උදා: `anishift-dl-vault`).
2. **Files ටික Push කරන්න:**
   - මේ `cloud_worker` folder එක ඇතුළේ තියෙන files ඔක්කොම (`.github/workflows/download_video.yml`, `dl_worker.py`, `requirements.txt`) ඒ repo එකේ root එකට push කරන්න.
3. **Cloudflare Worker (`download_worker/worker.js`) එකට Link කරන්න:**
   - GitHub Settings -> Developer settings -> Personal access tokens (Fine-grained) වලින් `Actions: Read & Write` permission තියෙන token එකක් හදාගන්න.
   - `download_worker` Cloudflare Worker එකේ `GITHUB_TOKEN` සහ `GITHUB_REPO` (`username/anishift-dl-vault`) environment variables දෙන්න.

