# Morning Podcast Briefing (`morning-podcast-briefing`)

A private daily podcast plus an email covering the podcasts and research sites you follow. Each item gets a
**summary**, **key takeaways** and **one memorable quote**. It plays in Apple Podcasts, so it works on CarPlay.

## How it works

```
every 3 hours ── ingest ──► new episodes/articles ──► transcript ──► Claude summary ──► state/items.json
                              (Apple directory / RSS)    (publisher transcript,
                                                          else OpenAI speech-to-text)

weekdays 5:00 AM HST ── build ──► Claude writes script ──► OpenAI voice ──► MP3 + feed.xml on GitHub Pages
                                                                       └──► email (quotes → takeaways → summaries)
```

* **Priority shows** (Founders, David Senra, Invest Like the Best) always come first and get a deeper treatment.
* Monday's briefing automatically includes everything published over the weekend.
* Days with nothing new produce no episode.

## One-time setup

1. **API keys.** Your Claude/ChatGPT subscriptions don't include API access, so set up these two pay-as-you-go accounts:
   * Anthropic: <https://console.anthropic.com> → add credit → *API Keys* → create key
   * OpenAI: <https://platform.openai.com> → add credit → *API keys* → create key
2. **Gmail app password** (for the email): turn on 2-Step Verification, then visit
   <https://myaccount.google.com/apppasswords>, create one named "Morning Briefing", and copy the 16 characters.
3. **GitHub secrets.** In the repo, go to *Settings → Secrets and variables → Actions → New repository secret* and add:

   | Name | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | Anthropic key |
   | `OPENAI_API_KEY` | OpenAI key |
   | `GMAIL_ADDRESS` | the Gmail address that sends the email |
   | `GMAIL_APP_PASSWORD` | the 16-character app password |
   | `EMAIL_TO` | *(optional)* where to send it; defaults to `GMAIL_ADDRESS` |

4. **First run:** go to the *Actions* tab → **Build morning briefing** → *Run workflow*.
5. **Turn on the feed:** go to *Settings → Pages → Build and deployment* → *Deploy from a branch* → `gh-pages` / `(root)` → Save.
6. **On your iPhone:** open Apple Podcasts → *Library* → **⋯** (top right) → **Follow a Show by URL** and enter
   `https://<your-github-username>.github.io/<repo-name>/feed.xml`.
   For hands-free mornings, set the show to download new episodes automatically. It will then appear in CarPlay under Library.

## Changing things

Everything is in [`config.yaml`](config.yaml):

* **Add or remove a podcast:** add or remove a line under `podcasts:`, using the name as it appears in Apple Podcasts.
* **Add a website:** add `- { name: "...", url: "https://..." }` under `websites:`.
* **Voice:** change `tts_voice` (`ash`, `coral`, `sage`, `ballad`, `verse`, `alloy`, `echo`, `onyx`, `nova`, `shimmer`)
  or edit `tts_style`.
* **Schedule:** edit the `cron` line in `.github/workflows/briefing.yml`. Times are in UTC; 15:00 UTC = 5:00 AM HST.

## Cost (rough)

For about 20 shows (about 22 new episodes a week), expect roughly **$45–55/month**:

* Transcription: about $20
* Claude summaries and scripts: about $18
* Voice: about $12

Shows that publish their own transcripts cost less. High-volume shows like *The Bill Simmons Podcast* are the biggest
cost driver; set `enabled: false` on one to pause it.

## Running locally

```bash
pip install -r requirements.txt   # also needs ffmpeg
python -m briefing resolve        # show which feed each podcast resolved to
DRY_RUN=1 python -m briefing ingest && DRY_RUN=1 python -m briefing build   # no API calls, silent audio
```
