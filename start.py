# -*- coding: utf-8 -*-
"""
Main entry point: runs FastAPI web server + Telegram bot together
"""
import asyncio
import threading
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)


def run_bot():
    """Run Telegram bot in a separate thread with its own event loop."""
    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    from dotenv import load_dotenv
    load_dotenv(os.path.join(_here, '.env'))
    load_dotenv(os.path.join(_here, '..', '.env'))

    from bot import main
    main(as_thread=True)


def run_web():
    """Run FastAPI web server."""
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("webapp:app", host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    # Seed DB first
    print("Seeding knowledge base...")
    import subprocess
    subprocess.run([sys.executable, os.path.join(_here, 'seed.py')], check=False)

    # Start bot in background thread
    print("Starting Telegram bot...")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Start web server (main thread)
    print("Starting web server...")
    run_web()
