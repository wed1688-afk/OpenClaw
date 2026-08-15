# README

## Higgsfield AI integration

OpenClaw can generate images and videos through the official
[Higgsfield platform API](https://higgsfield.ai/) (text-to-image via the
Soul model, image-to-video via the DoP model).

### Setup

1. Get API credentials from the Higgsfield Cloud dashboard
   (API access is included in paid Creator plans).
2. Export them:

   ```bash
   export HF_API_KEY=your-key
   export HF_SECRET=your-secret
   ```

3. Install dependencies: `pip install -r requirements.txt`

### Python usage

```python
from tools.higgsfield import HiggsfieldClient

with HiggsfieldClient() as client:
    # Text -> image
    job_set = client.text2image(prompt="a cat astronaut, cinematic")
    job_set = client.wait_for_completion(job_set["id"])
    print(HiggsfieldClient.result_urls(job_set))

    # Image -> video
    job_set = client.image2video(
        image_url="https://example.com/cat.png",
        prompt="slow dolly zoom",
        model="dop-standard",
    )
```

Generation is asynchronous: submitting returns a *job set*, which you poll
with `wait_for_completion()` (or receive via webhook by passing
`webhook_url=`). Motion and style presets are discoverable via
`client.list_motions()` and `client.list_soul_styles()`.

### HTTP API

Run the backend with `uvicorn backend.main:app` and use:

- `POST /higgsfield/text2image` — body: `{"prompt": "..."}`
- `POST /higgsfield/image2video` — body: `{"image_url": "...", "prompt": "..."}`
- `GET /higgsfield/jobs/{job_set_id}` — poll status and result URLs
