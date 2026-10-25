# WhatsApp Local Handshake Test

> For the integrated Android + WhatsApp backend, run `../backend/app.py` and
> follow `../backend/README.md`. This standalone server remains the original
> messaging proof of concept; do not run it alongside the integrated backend.

Prove that WhatsApp can talk to a Python program running on your Windows laptop — no cloud hosting needed.

**What happens:**

1. You send **"Hi"** from your personal WhatsApp to a Meta test number.
2. Your laptop receives the message through a webhook.
3. Your terminal shows: `WhatsApp message received: Hi`
4. Your laptop sends a reply through the official WhatsApp Cloud API.
5. You receive: **"Hello! Smart Nagrik local backend is connected successfully ✅"**

---

## Project files

| File | Purpose |
|---|---|
| `app.py` | Flask server with health check + webhook endpoints |
| `test_local.py` | Automated local tests (no real WhatsApp needed) |
| `.env.example` | Placeholder credentials — copy to `.env` and fill in |
| `.gitignore` | Keeps your `.env` secrets out of Git |
| `requirements.txt` | Python packages (Flask, requests, python-dotenv) |

---

## Quick start (local testing only)

These steps verify the server works on your laptop. No Meta account needed yet.

### 1. Open PowerShell and navigate to the project

```powershell
cd "c:\Users\pkuma\New folder (3)\whatsapp-handshake"
```

### 2. Create a virtual environment

```powershell
python -m venv venv
```

### 3. Activate it

```powershell
.\venv\Scripts\Activate.ps1
```

> **If you get an "execution policy" error**, run this once first:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

### 4. Install packages

```powershell
pip install -r requirements.txt
```

### 5. Create your `.env` file

```powershell
Copy-Item .env.example .env
```

The default test values in `.env.example` are enough for local testing.

### 6. Start the server

```powershell
python app.py
```

You should see:

```
🚀 Starting WhatsApp Handshake Server on http://localhost:8000
 * Running on http://127.0.0.1:8000
```

### 7. Run local tests (in a second PowerShell window)

```powershell
cd "c:\Users\pkuma\New folder (3)\whatsapp-handshake"
.\venv\Scripts\Activate.ps1
python test_local.py
```

Expected output:

```
=======================================================
 WhatsApp Handshake -- LOCAL Simulation Tests
 (This does NOT test a real WhatsApp connection)
=======================================================

-- Test 1: Health endpoint (GET /) --
   Status: 200  =>  [PASS]

-- Test 2: Webhook verification -- correct token --
   Status: 200  =>  [PASS]

-- Test 3: Webhook verification -- wrong token --
   Status: 403  =>  [PASS]

-- Test 4: Signed webhook POST (simulated WhatsApp message) --
   Status: 200  =>  [PASS]

-- Test 5: Meta webhook POST with bad signature --
   Status: 403  =>  [PASS]

-- Test 6: Twilio live endpoint -- 'Hi' message --
   Status: 200  =>  [PASS]

-- Test 7: Twilio live endpoint -- other message --
   Status: 200  =>  [PASS]

-- Test 8: Twilio signature validation -- valid signature (Cloudflare Tunnel) --
   Status: 200  =>  [PASS]

-- Test 9: Twilio signature validation -- bad signature (rejected) --
   Status: 403  =>  [PASS]

=======================================================
 Results: 9/9 tests passed
=======================================================
```

> **Important:** These tests are LOCAL SIMULATIONS only. They prove your code works, but they do NOT prove a real WhatsApp connection. For that, continue to the next section.

---

## Real WhatsApp setup (Phase 3)

This section walks you through connecting your local server to real WhatsApp messages.

### Step 1: Create a Meta Developer account

1. Go to [developers.facebook.com](https://developers.facebook.com/).
2. Click **Get Started** and log in with your Facebook account.
3. Complete any verification steps Meta asks for.

### Step 2: Create an app

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps/).
2. Click **Create App**.
3. Choose app type: **Business** (or **Other** → **Business**).
4. Give it a name like `Smart Nagrik Handshake`.
5. Click **Create App**.

### Step 3: Add WhatsApp to your app

1. On your app's dashboard, find **WhatsApp** in the product list.
2. Click **Set Up**.
3. If asked to connect a Business account, click **Continue** (Meta creates a test business account for you).

### Step 4: Get your credentials

Go to **WhatsApp** → **API Setup** in the left sidebar. You will see:

| What to find | Where it is |
|---|---|
| **Temporary Access Token** | The long text string under "Temporary access token" — click **Copy** |
| **Phone Number ID** | Shown below the token, a numeric string like `123456789012345` |
| **Test Phone Number** | The WhatsApp number Meta assigns (e.g., +1 555 xxx xxxx) |
| **Graph API Version** | Shown in the API call example URL (e.g., `v21.0`) |

### Step 5: Get your App Secret

1. Go to **App Settings** → **Basic** in the left sidebar.
2. Click **Show** next to **App Secret**.
3. Copy the secret string.

### Step 6: Add your personal phone as a test recipient

On the **API Setup** page:

1. Under **"To" phone number**, click **Manage phone number list**.
2. Click **Add phone number**.
3. Enter your personal WhatsApp phone number (with country code, e.g., `+91 98765 43210`).
4. Meta will send a verification code to your WhatsApp. Enter it.
5. Your number is now approved to receive test messages.

### Step 7: Update your `.env` file

Open `.env` in a text editor and replace the placeholder values:

```ini
WHATSAPP_ACCESS_TOKEN=EAAxxxxxxx...your_real_token...
WHATSAPP_PHONE_NUMBER_ID=123456789012345
GRAPH_API_VERSION=v21.0
WEBHOOK_VERIFY_TOKEN=my_secret_verify_token_123
META_APP_SECRET=abcdef1234567890abcdef1234567890
```

> **WEBHOOK_VERIFY_TOKEN** is a secret YOU make up. Use any random string. You'll enter the same string in the Meta dashboard later.

### Step 8: Install Cloudflare Tunnel (cloudflared)

Cloudflare Quick Tunnel creates a temporary HTTPS URL that forwards traffic to your laptop. No Cloudflare account needed.

#### Option A: Download directly

1. Go to [github.com/cloudflare/cloudflared/releases/latest](https://github.com/cloudflare/cloudflared/releases/latest).
2. Download `cloudflared-windows-amd64.exe`.
3. Put it somewhere easy, like `C:\Tools\cloudflared.exe`.

#### Option B: Install with winget

```powershell
winget install Cloudflare.cloudflared
```

#### Verify installation

```powershell
cloudflared --version
```

### Step 9: Start your server + tunnel

You need **two PowerShell windows** running at the same time.

**Window 1 — Flask server:**

```powershell
cd "c:\Users\pkuma\New folder (3)\whatsapp-handshake"
.\venv\Scripts\Activate.ps1
python app.py
```

**Window 2 — Cloudflare Tunnel:**

```powershell
cloudflared tunnel --url http://localhost:8000
```

> If you downloaded the `.exe` manually:
> ```powershell
> C:\Tools\cloudflared.exe tunnel --url http://localhost:8000
> ```

After a few seconds, you'll see output like:

```
+----------------------------+---------------------------------------------------+
|          TUNNEL            |                       URL                         |
+----------------------------+---------------------------------------------------+
|                            | https://random-words-here.trycloudflare.com       |
+----------------------------+---------------------------------------------------+
```

**Copy that `https://...trycloudflare.com` URL.** You'll need it next.

> The URL changes every time you restart the tunnel. That's fine for testing.

### Step 10: Register the webhook in Meta Developer dashboard

1. Go to your app on [developers.facebook.com](https://developers.facebook.com/).
2. In the left sidebar, go to **WhatsApp** → **Configuration**.
3. Under **Webhook**, click **Edit**.
4. Fill in:
   - **Callback URL:** `https://random-words-here.trycloudflare.com/webhook`
     (paste YOUR tunnel URL, add `/webhook` at the end)
   - **Verify token:** `my_secret_verify_token_123`
     (must match `WEBHOOK_VERIFY_TOKEN` in your `.env`)
5. Click **Verify and Save**.

If successful, your Flask terminal will show:

```
✅ Webhook verification succeeded!
```

If it fails, check:
- Is the Flask server running?
- Is the Cloudflare tunnel running?
- Does the verify token match exactly?

### Step 11: Subscribe to messages

After verifying the webhook:

1. On the same **Configuration** page, find the **Webhook fields** section.
2. Find the row labelled **messages**.
3. Click **Subscribe** (or toggle it on).

This tells Meta to forward incoming WhatsApp messages to your webhook.

### Step 12: Send your first real message!

1. Open WhatsApp on your phone.
2. Find the Meta test number (from Step 4) in your contacts, or start a new chat with it.
3. Send: **Hi**
4. Watch your Flask terminal — you should see:

```
📩 Webhook event received
📱 WhatsApp message received: Hi
✅ Reply sent to 91xxxxxxxxxx
```

5. Check your phone — you should receive:

> **Hello! Smart Nagrik local backend is connected successfully ✅**

🎉 **Congratulations! Your laptop is talking to WhatsApp!**

---

## Twilio WhatsApp Setup ("Try out WhatsApp" Trial Environment)

The new Twilio WhatsApp trial environment requires outbound messages to be sent through the **Twilio Messages API** using a pre-approved template with a `ContentSid` (starting with `HX...`). Direct TwiML XML responses in webhook returns are not delivered.

### Step 1: Find your Credentials in Twilio Console

Log in to [console.twilio.com](https://console.twilio.com/):

| Variable in `.env` | Where to find it in Twilio Dashboard | Example |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | Dashboard homepage → **Account Info** → **Account SID** | `ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `TWILIO_AUTH_TOKEN` | Dashboard homepage → **Account Info** → **Auth Token** | `your_auth_token_here` |
| `TWILIO_WHATSAPP_NUMBER` | **Develop** → **Messaging** → **Try it out** → **Send a WhatsApp message** (shown under "Twilio Phone Number" or "From") | `whatsapp:+14155238886` |
| `TWILIO_CONTENT_SID` | **Develop** → **Messaging** → **Try it out** → **Send a WhatsApp message** (or **Messaging** → **Content Template Builder**). Look at the sample template code on the page; it starts with `HX` | `HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `TWILIO_CONTENT_VARIABLES` | If the template has placeholders (like `{{1}}`), set as JSON string | `{"1":"Smart Nagrik"}` |

### Step 2: Configure Webhook in Twilio Console

1. In the left navigation, go to **Develop** → **Messaging** → **Try it out** → **Send a WhatsApp message**.
2. Click on the **Sandbox Settings** tab (or **Endpoint Configuration**).
3. Under **WHEN A MESSAGE COMES IN**:
   - Set URL to: `https://your-tunnel.trycloudflare.com/twilio/webhook`
   - Set HTTP method to: **POST**
4. Click **Save**.

### Step 3: Put Credentials in `.env`

Edit your `.env` file:

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
TWILIO_CONTENT_SID=HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_CONTENT_VARIABLES={"1":"Smart Nagrik"}
```

### Step 4: Start Server & Tunnel

**Window 1 (Server):**
```powershell
cd "c:\Users\pkuma\New folder (3)\whatsapp-handshake"
.\venv\Scripts\Activate.ps1
python app.py
```

**Window 2 (Cloudflare Tunnel):**
```powershell
cloudflared tunnel --url http://localhost:8000
```

### Step 5: Test from WhatsApp

1. Send **"Hi"** from your phone to Twilio's WhatsApp number.
2. Watch your Flask terminal logs:
   ```text
   📱 WhatsApp message received (via Twilio): 'Hi' from whatsapp:+91****1234
   📤 Dispatching approved template (ContentSid=HX...) to whatsapp:+91****1234
   📨 Twilio message dispatched: SID=SM..., Initial Status=queued, To=whatsapp:+91****1234
   📊 Delivery status check: SID=SM..., Status=delivered (or sent)
   ```
3. Your phone receives the approved WhatsApp template message!

---

## Troubleshooting

### "Webhook verification failed"

- Make sure `WEBHOOK_VERIFY_TOKEN` in `.env` matches exactly what you entered in the Meta dashboard.
- Make sure the Cloudflare tunnel is running and the URL ends with `/webhook`.

### "Invalid OAuth access token" (error 190)

- The temporary access token expires after ~24 hours. Go back to **API Setup** and copy a fresh one.
- Paste it into `.env` and restart the server.

### "Recipient phone number not in allowed list" (error 131030)

- You need to add your personal number as a test recipient (Step 6).
- Make sure you verified it with the code Meta sent.

### Server shows nothing when I send a message

- Check that you subscribed to the **messages** webhook field (Step 11).
- Check the Cloudflare tunnel is still running (it can disconnect).
- Try restarting both the server and the tunnel.

### Cloudflare tunnel gives a new URL

- That's normal. Update the **Callback URL** in the Meta dashboard every time the tunnel URL changes.

### I get a reply but it's a template message, not my custom text

- The first message in a new conversation must be a Meta-approved template. Send a template first from the API Setup page, then your custom replies will work.

---

## How it works (simple explanation)

```
Your Phone                Meta Servers              Your Laptop
    |                          |                          |
    |--- "Hi" ---------------→|                          |
    |                          |--- POST /webhook ------→|  (through Cloudflare tunnel)
    |                          |                          |
    |                          |                          |  Terminal: "Message received: Hi"
    |                          |                          |
    |                          |←--- Send reply ---------|  (WhatsApp Cloud API)
    |                          |                          |
    |←-- "Hello! Smart..." ---|                          |
    |                          |                          |
```

- **Cloudflare tunnel** gives your laptop a temporary public HTTPS URL.
- **Meta webhook** sends incoming messages to that URL.
- **Your Flask server** processes the message and calls the WhatsApp Cloud API to reply.
- All processing happens on YOUR laptop. No cloud server needed.

---

## Security notes

- Secrets are in `.env` (never committed to Git thanks to `.gitignore`).
- Incoming webhooks are validated using HMAC-SHA256 signatures with your App Secret.
- The temporary access token expires after ~24 hours — this is intentional for testing.
- The Cloudflare tunnel URL is random and temporary.

---

## What this project is NOT

- ❌ Not a chatbot, CRM, or production app
- ❌ No database, no AI, no frontend
- ❌ No unofficial libraries (Baileys, WhatsApp Web automation)
- ✅ Just a simple proof: your laptop ↔ WhatsApp

---

*Built for Smart Nagrik — a simple connectivity test.*
