# brainrot-classifier-prototype

Classifies short videos as brain rot or not with Gemini, and a Chrome extension (`extension-counter/`) that counts brain rot videos while you scroll Instagram or TikTok.

## BrainRot Counter setup

The extension sends each video to the Gemini model running on your own computer, so there are two parts: start the model, then load the extension.

### 1. Get the code

Clone the repo, or download it as a zip and unzip it.

### 2. Start the model

You need Python 3.10 or newer. In the repo folder:

```
pip install -r requirements.txt
```

Copy `.env.example` to `.env` (`copy .env.example .env` on Windows, `cp .env.example .env` on Mac or Linux). Open `.env` and replace `your-key-here` with your Gemini API key from https://aistudio.google.com/apikey.

Start the server and leave the terminal open while you use the extension:

```
python -m uvicorn brainrot.web.server:app --port 8000
```

Check it at http://localhost:8000/health. You should see `"ok": true` and `"has_api_key": true`.

### 3. Load the extension in Chrome

1. Go to `chrome://extensions`.
2. Turn on Developer mode (top right).
3. Click Load unpacked and select the `extension-counter` folder inside the repo. If you get "Manifest file is missing", you picked the folder above it.
4. Click the puzzle-piece icon in the toolbar and pin BrainRot Counter.

### 4. Use it

- Open instagram.com/reels or tiktok.com and play a video. The counter appears in the top-right corner, and you can drag it anywhere.
- After about 4 seconds it shows "asking the model…", and the video's score arrives a few seconds later. A score of 50 or more counts as brain rot.
- Each video counts once, even if you scroll back to it. Videos you swipe past in under 4 seconds aren't counted.
- Click the toolbar icon to turn the counter on or off, see today's numbers, or reset them. The badge on the icon shows today's brain rot count.

### If something's wrong

| What you see | What to do |
| --- | --- |
| "model offline" | The server from step 2 isn't running, or isn't on port 8000. |
| Popup says the API key isn't set | Check `.env`, then restart the server. |
| No counter on the page | Refresh the Instagram or TikTok tab. The extension only attaches to pages opened after it was installed. |
| "model error" | Read the error in the server terminal. |
| You pulled new code | Click the reload arrow on the BrainRot Counter card in `chrome://extensions`, then refresh the tab. |

Everyone runs the model locally with their own key, because the extension looks for it at `localhost:8000`. To use a shared, deployed server instead, set `API_URL` in `extension-counter/config.js` to its address and skip step 2.

More detail on how the extension counts and what `/predict` returns is in [`extension-counter/README.md`](extension-counter/README.md).

## Command line

```
pip install -e .
brainrot classify video.mp4            # one or more files, folders or URLs
brainrot eval labels.csv               # accuracy against a labelled CSV (source,label[,context])
brainrot serve                         # web UI and the extension's API on port 8000
```
