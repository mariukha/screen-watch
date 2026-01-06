# ScreenWatch

**ScreenWatch** is a minimalistic desktop application that monitors a specific region of your screen for visual changes. When a change is detected, it automatically sends a screenshot notification to your Telegram via a bot.

Built with Python and PyQt6, it features a clean dark mode interface and minimizes to the system tray.

## Features

- **Custom Region**: Draw a selection box anywhere on your screen to monitor.
- **Real-time Detection**: Uses image comparison to detect changes instantly.
- **Telegram Integration**: Receive a screenshot and score of the change directly in your chat.
- **Adjustable Sensitivity**: Fine-tune the threshold to ignore minor flickering or noise.
- **Minimalistic UI**: Dark theme, distraction-free design.

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/screen-watch.git
   cd screen-watch
   ```

2. **Set up a virtual environment (recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Setup Telegram Bot

To receive notifications, you need a Telegram Bot:

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the instructions to create a bot.
3. Copy the **HTTP API Token** provided (this is your `Bot Token`).
4. Start a chat with your new bot.
5. Search for **@userinfobot** (or any ID bot) to find your numeric **Chat ID**.

## Usage

1. **Run the application:**
   ```bash
   python screen_watcher_pro.py
   ```

2. **Configure:**
   - Paste your **Bot Token** and **Chat ID** into the fields.
   - Click **"Test"** to verify the connection.

3. **Select Region:**
   - Click **"Select Region"**.
   - Click and drag to draw a box around the area you want to watch.

4. **Start:**
   - Click **"Start Monitoring"**.
   - The app status will change to "Active".
   - You can minimize the app; it will keep running in the background.

## Configuration

Your settings (tokens, selected region, threshold) are saved locally in a `config.json` file for convenience. 

**Security Note:** The `config.json` file is ignored by Git (`.gitignore`) to prevent your secrets from being uploaded to the repository.

## Requirements

- Python 3.8+
- PyQt6
- Pillow
- PyAutoGUI
- Numpy
- Requests
