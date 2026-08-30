# SELF-HOSTED speaker recognition — 3D-Speaker (local, no 3rd-party service)

This project's speaker recognition runs **entirely on your own machine**, powered by
the open-source [3D-Speaker](https://github.com/modelscope/3D-Speaker) toolkit
(Apache-2.0). There is **no dependence on a 3rd-party SaaS**:

- A pretrained model (default **CAM++**, ~7.2M params) is downloaded **once** from
  [ModelScope](https://modelscope.cn) on first start.
- After that, all verification/registration is **offline** (CPU or GPU).
- Enrolled voice samples are stored **locally** as `.wav` files + a small JSON
  index — no external MySQL/Postgres, no cloud calls.
- Scoring uses the ModelScope **light** speaker-verification pipeline: `/identify`
  compares the incoming utterance against every enrolled sample and returns the
  best similarity score (a handful of users ⇒ plenty fast on CPU).

It exposes the same HTTP contract the server already uses (`/voiceprint/health`,
`/voiceprint/identify`, `/voiceprint/register`), so the existing
`core/utils/voiceprint_provider.py` works unchanged.

---

## 1. Deploy the local voiceprint service

```bash
cd <repo-root>
./run-voiceprint.sh up        # builds + starts on port 8005 (first run downloads the model)
./run-voiceprint.sh logs      # watch "model loaded — running locally"
```

Verify it's healthy:

```bash
curl "http://<lan-ip>:8005/voiceprint/health?key=abcd"
# -> {"status":"healthy","total_voiceprints":0}
```

Optional: change model / threshold / key in `docker/voiceprint-local/voiceprint-local.yaml`
or via env (`VOICEPRINT_MODEL_ID`, `VOICEPRINT_THRESHOLD`, `VOICEPRINT_KEY`, ...).

| Model (ModelScope id) | Params | Notes |
|---|---|---|
| `iic/speech_campplus_sv_zh_en_16k-common_advanced` | 7.2 M | default — fast on CPU |
| `iic/speech_eres2net_sv_zh-cn_16k-common` | 6.6 M | higher accuracy |
| `iic/speech_eres2netv2_sv_zh-cn_16k-common` | 17.8 M | best EER, heavier |

---

## 2. Enable it in the xiaozhi-server

Add to `main/xiaozhi-server/data/.config.yaml`:

```yaml
voiceprint:
  url: http://<lan-ip>:8005/voiceprint/health?key=abcd
  similarity_threshold: 0.55
  admin_name: "Mr Blue"
  admin_password: "abcd1234"
  reserved_names:
    - "admin"
    - "quản trị viên"
  # FEATURE FLAG — false (default) keeps legacy behavior:
  #   no onboarding, no admin re-sample, memory not gated.
  enroll_enabled: true
  user_store_path: data/voice_users.json
```

Restart the server. Logs should show:
`声纹识别功能已在连接时动态启用 (admin=Mr Blue, users=..., multi-user feature=True)`.

### Feature flag: `voiceprint.enroll_enabled`

| `enroll_enabled` | Behavior |
|---|---|
| `false` (default) | Voice recognition (identity) works, but **no** multi-user onboarding, **no** admin re-sample, memory writes **not** gated (legacy). |
| `true` | Full multi-user feature: unknown-voice onboarding, admin re-sample, admin-only memory permission. |

---

## 3. Multi-user flow

### Mr Blue (admin)
- **Reserved name** — no one else can register as "Mr Blue".
- **Memory permission** — only Mr Blue may save/change long-term memory
  (`mem:*` tags). Others are politely blocked server-side.

### New user onboarding
1. A voice that isn't recognized speaks → robot:
   `"Xin chào! Mình chưa nhận ra giọng nói của bạn. Bạn tên gì ạ?"`
2. User says their name (e.g. "An") → robot:
   `"Cảm ơn An! Vui lòng nói lại câu: 'Xin chào, mình tên là An'..."`
3. User repeats → robot registers the voice sample under that name and replies
   `"Đã lưu giọng nói của An. Rất vui được gặp bạn!"`

Next time An speaks, the robot recognizes them by name.

### Admin re-sample (tag-based, like `mv:`/`mem:`/`sleep`)
The flow is driven by the LLM appending a `vpr:resample` tag:
1. Mr Blue says: **"tái lập mẫu giọng nói admin"**.
2. The LLM replies with a confirmation that ends in `vpr:resample`
   (e.g. *"Vâng ạ. Vui lòng nói mật khẩu xác nhận nhé. vpr:resample"*).
3. The server strips the tag from speech, verifies the speaker is the admin, and
   arms the re-sample state (asks for the password).
4. Mr Blue says the password **"abcd1234"** → robot asks him to read a longer
   sentence (~5 s) → his voiceprint is updated.

> Only the admin may trigger it, and only when `voiceprint.enroll_enabled: true`
> — the prompt directive is injected only when the feature flag is on.

---

## 4. Storage / persistence

- Voice embeddings: `docker/voiceprint-local/data/embeddings/*.npy`
  (Docker volume `voiceprint-data`).
- User name ↔ speaker_id mapping: `main/xiaozhi-server/data/voice_users.json`
  (`voiceprint.user_store_path`).

Both persist across restarts.

---

## 5. Notes & tuning

- **Threshold**: `VOICEPRINT_THRESHOLD` (0.55 default) is the minimum cosine
  similarity for `/identify` to return a speaker. The server also applies its own
  `voiceprint.similarity_threshold`. If the robot often says it doesn't know the
  person, lower it slightly; if it confuses two people, raise it.
- **First-run**: the model (~200–400 MB) downloads from ModelScope once; keep
  `voiceprint-cache` volume so it isn't re-downloaded.
- **GPU**: set `VOICEPRINT_DEVICE=cuda:0` (needs a CUDA build of torch).
- The service is only reachable on your LAN — keep it that way; `key` is a simple
  shared token, not a substitute for a firewall.
