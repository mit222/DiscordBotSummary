import discord
from discord.ext import commands, tasks
import openai
import os
from datetime import datetime, timedelta, time as dt_time
import json
from collections import defaultdict
from dotenv import load_dotenv
import aiohttp
import asyncio

# Load environment variables from .env file
load_dotenv()

# Load environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Initialize OpenAI
openai.api_key = OPENAI_API_KEY

# Bot setup with intents
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Store last read timestamps for each user per channel
user_last_read = defaultdict(dict)
DATA_FILE = 'user_data.json'

# Store Fear & Greed Index scheduler settings
SCHEDULER_FILE = 'fng_scheduler.json'
fng_scheduler_settings = {
    'enabled': False,
    'channel_id': None,
    'time': '20:00'  # 8 PM
}

def load_user_data():
    """Load user last read timestamps from file"""
    global user_last_read
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                user_last_read = defaultdict(dict, {k: v for k, v in data.items()})
    except Exception as e:
        print(f"Error loading user data: {e}")

def save_user_data():
    """Save user last read timestamps to file"""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(dict(user_last_read), f)
    except Exception as e:
        print(f"Error saving user data: {e}")

def load_scheduler_settings():
    """Load Fear & Greed Index scheduler settings"""
    global fng_scheduler_settings
    try:
        if os.path.exists(SCHEDULER_FILE):
            with open(SCHEDULER_FILE, 'r') as f:
                fng_scheduler_settings = json.load(f)
    except Exception as e:
        print(f"Error loading scheduler settings: {e}")

def save_scheduler_settings():
    """Save Fear & Greed Index scheduler settings"""
    try:
        with open(SCHEDULER_FILE, 'w') as f:
            json.dump(fng_scheduler_settings, f)
    except Exception as e:
        print(f"Error saving scheduler settings: {e}")

async def fetch_messages(channel, after_time=None, limit=1000):
    """Fetch messages from a channel after a specific time"""
    messages = []
    try:
        async for message in channel.history(limit=limit, after=after_time, oldest_first=True):
            if not message.author.bot:  # Skip bot messages
                messages.append({
                    'author': message.author.name,
                    'content': message.content,
                    'timestamp': message.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'attachments': [att.url for att in message.attachments]
                })
    except discord.Forbidden:
        return None
    return messages

async def summarize_with_openai(messages, summary_type="full"):
    """Use OpenAI to summarize messages"""
    if not messages:
        return "No messages to summarize."
    
    # Format messages for GPT
    message_text = "\n".join([
        f"[{msg['timestamp']}] {msg['author']}: {msg['content']}" 
        for msg in messages
    ])
    
    # ANPASSEN: Hier kannst du den Prompt ändern
    if summary_type == "full":
        prompt = f"""Bitte erstelle eine umfassende Zusammenfassung der folgenden Discord-Nachrichten auf Deutsch.

Gliedere die Zusammenfassung nach Hauptthemen und wichtigen Punkten:
- Hauptthemen und Diskussionen
- Wichtige Entscheidungen oder Ankündigungen
- Offene Fragen und To-Dos

Nachrichten:
{message_text}

Zusammenfassung:"""
    else:  # update type
        prompt = f"""Erstelle eine kurze Zusammenfassung der neuen Discord-Nachrichten auf Deutsch.

Fokussiere dich auf:
- Die wichtigsten neuen Themen
- Direkte Erwähnungen oder Fragen an Nutzer
- Dringende Punkte

Nachrichten:
{message_text}

Kurze Zusammenfassung:"""
    
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",  # oder "gpt-4o" für bessere Qualität (teurer)
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Assistent, der Discord-Konversationen klar und prägnant auf Deutsch zusammenfasst."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,  # Erhöhen für längere Zusammenfassungen
            temperature=0.7  # 0.0-1.0: Niedriger = faktischer, Höher = kreativer
        )
        
        summary = response.choices[0].message.content.strip()
        
        # Debug: Print zu Render Logs
        print(f"OpenAI Response length: {len(summary)}")
        print(f"First 100 chars: {summary[:100]}")
        
        return summary
    except Exception as e:
        return f"Error generating summary: {str(e)}"

@bot.event
async def on_ready():
    """Bot startup event"""
    load_user_data()
    load_scheduler_settings()
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guild(s)')
    
    # Start Fear & Greed Index scheduler if enabled
    if fng_scheduler_settings.get('enabled', False):
        if not fear_greed_scheduler.is_running():
            fear_greed_scheduler.start()
            print(f"Fear & Greed Index scheduler started for channel {fng_scheduler_settings.get('channel_id')}")

async def fetch_fear_greed_index():
    """Fetch the current Fear & Greed Index from API"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.alternative.me/fng/') as response:
                if response.status == 200:
                    data = await response.json()
                    return data['data'][0]
                else:
                    return None
    except Exception as e:
        print(f"Error fetching Fear & Greed Index: {e}")
        return None

def get_fng_emoji(value):
    """Get emoji based on Fear & Greed value"""
    value = int(value)
    if value <= 25:
        return "😱"  # Extreme Fear
    elif value <= 45:
        return "😰"  # Fear
    elif value <= 55:
        return "😐"  # Neutral
    elif value <= 75:
        return "😊"  # Greed
    else:
        return "🤑"  # Extreme Greed

def format_fng_message(fng_data):
    """Format Fear & Greed Index data into a Discord message"""
    value = int(fng_data['value'])
    classification = fng_data['value_classification']
    timestamp = datetime.fromtimestamp(int(fng_data['timestamp'])).strftime('%d.%m.%Y %H:%M')
    
    emoji = get_fng_emoji(value)
    
    # Create a visual bar
    filled = int(value / 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    
    message = f"""
📊 **Crypto Fear & Greed Index**

{emoji} **{value}/100** - {classification}

{bar}

🕐 Stand: {timestamp}

**Legende:**
😱 0-25: Extreme Fear
😰 26-45: Fear
😐 46-55: Neutral
😊 56-75: Greed
🤑 76-100: Extreme Greed
"""
    return message

@tasks.loop(time=dt_time(hour=20, minute=0))  # 20:00 Uhr = 8 PM
async def fear_greed_scheduler():
    """Daily task to post Fear & Greed Index"""
    if not fng_scheduler_settings.get('enabled', False):
        return
    
    channel_id = fng_scheduler_settings.get('channel_id')
    if not channel_id:
        return
    
    channel = bot.get_channel(int(channel_id))
    if not channel:
        print(f"Error: Channel {channel_id} not found")
        return
    
    print(f"Posting Fear & Greed Index to channel {channel.name}")
    
    fng_data = await fetch_fear_greed_index()
    if fng_data:
        message = format_fng_message(fng_data)
        await channel.send(message)
    else:
        await channel.send("❌ Konnte Fear & Greed Index nicht abrufen.")

@fear_greed_scheduler.before_loop
async def before_scheduler():
    """Wait until bot is ready before starting scheduler"""
    await bot.wait_until_ready()

@bot.command(name='updateme')
async def update_me(ctx, *channels: discord.TextChannel):
    """
    Get a summary of new messages since your last check for specified channels.
    Usage: !updateme #channel1 #channel2
    """
    print(f"[DEBUG] updateme called by {ctx.author} for channels: {[c.name for c in channels]}")
    
    if not channels:
        await ctx.send("❌ Please mention at least one channel. Usage: `!updateme #channel1 #channel2`")
        return
    
    await ctx.send("🔍 Fetching new messages... This may take a moment.")
    
    user_id = str(ctx.author.id)
    all_summaries = []
    
    for channel in channels:
        # Get last read time for this user and channel
        last_read = user_last_read[user_id].get(str(channel.id))
        
        if last_read:
            after_time = datetime.fromisoformat(last_read)
        else:
            # First time: get messages from last 24 hours
            after_time = datetime.utcnow() - timedelta(days=1)
        
        messages = await fetch_messages(channel, after_time=after_time)
        
        if messages is None:
            all_summaries.append(f"❌ **{channel.mention}**: No permission to read this channel.")
            continue
        
        if not messages:
            all_summaries.append(f"✅ **{channel.mention}**: No new messages since your last check.")
            continue
        
        # Generate summary
        summary = await summarize_with_openai(messages, summary_type="update")
        
        all_summaries.append(f"📊 **{channel.mention}** ({len(messages)} new messages):\n{summary}")
        
        # Update last read time
        user_last_read[user_id][str(channel.id)] = datetime.utcnow().isoformat()
    
    # Save updated timestamps
    save_user_data()
    
    # Send summaries (split if too long)
    full_response = "\n\n".join(all_summaries)
    
    if len(full_response) > 1900:  # Discord limit is 2000, keep some buffer
        # Split into multiple messages
        for i, summary in enumerate(all_summaries):
            if len(summary) > 1900:
                # If single summary is too long, split it further
                chunks = [summary[j:j+1900] for j in range(0, len(summary), 1900)]
                for chunk in chunks:
                    await ctx.send(chunk)
            else:
                await ctx.send(summary)
    else:
        await ctx.send(full_response)

@bot.command(name='summarize')
async def summarize(ctx, channel: discord.TextChannel = None, hours: int = 24):
    """
    Get a full summary of messages from a channel within a time period.
    Usage: !summarize #channel [hours]
    Example: !summarize #general 48
    """
    print(f"[DEBUG] summarize called by {ctx.author} for channel: {channel}, hours: {hours}")
    
    if channel is None:
        channel = ctx.channel
    
    if hours < 1 or hours > 720:  # Max 30 days
        await ctx.send("❌ Please specify hours between 1 and 720 (30 days).")
        return
    
    await ctx.send(f"🔍 Summarizing {channel.mention} for the last {hours} hours... This may take a moment.")
    
    # Calculate time range
    after_time = datetime.utcnow() - timedelta(hours=hours)
    
    messages = await fetch_messages(channel, after_time=after_time, limit=2000)
    
    if messages is None:
        await ctx.send(f"❌ I don't have permission to read {channel.mention}.")
        return
    
    if not messages:
        await ctx.send(f"ℹ️ No messages found in {channel.mention} for the last {hours} hours.")
        return
    
    # Generate summary
    summary = await summarize_with_openai(messages, summary_type="full")
    
    response = f"📊 **Summary of {channel.mention}** (Last {hours} hours, {len(messages)} messages):\n\n{summary}"
    
    if len(response) > 1900:
        # Split into multiple messages if too long
        header = f"📊 **Summary of {channel.mention}** (Last {hours} hours, {len(messages)} messages):\n\n"
        await ctx.send(header)
        
        # Send summary in chunks
        chunks = [summary[i:i+1900] for i in range(0, len(summary), 1900)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(response)

@bot.command(name='fng')
async def fear_greed_now(ctx):
    """
    Get the current Fear & Greed Index
    Usage: !fng
    """
    await ctx.send("🔍 Hole aktuellen Fear & Greed Index...")
    
    fng_data = await fetch_fear_greed_index()
    if fng_data:
        message = format_fng_message(fng_data)
        await ctx.send(message)
    else:
        await ctx.send("❌ Konnte Fear & Greed Index nicht abrufen.")

@bot.command(name='fng_start')
async def fng_start(ctx, channel: discord.TextChannel = None):
    """
    Start automatic daily Fear & Greed Index posts
    Usage: !fng_start #channel
    """
    if channel is None:
        channel = ctx.channel
    
    fng_scheduler_settings['enabled'] = True
    fng_scheduler_settings['channel_id'] = str(channel.id)
    save_scheduler_settings()
    
    if not fear_greed_scheduler.is_running():
        fear_greed_scheduler.start()
    
    await ctx.send(f"✅ Fear & Greed Index wird ab jetzt täglich um 20:00 Uhr in {channel.mention} gepostet!")

@bot.command(name='fng_stop')
async def fng_stop(ctx):
    """
    Stop automatic daily Fear & Greed Index posts
    Usage: !fng_stop
    """
    fng_scheduler_settings['enabled'] = False
    save_scheduler_settings()
    
    if fear_greed_scheduler.is_running():
        fear_greed_scheduler.cancel()
    
    await ctx.send("⏹️ Automatische Fear & Greed Index Posts wurden gestoppt.")

@bot.command(name='fng_status')
async def fng_status(ctx):
    """
    Check the status of Fear & Greed Index scheduler
    Usage: !fng_status
    """
    enabled = fng_scheduler_settings.get('enabled', False)
    channel_id = fng_scheduler_settings.get('channel_id')
    time = fng_scheduler_settings.get('time', '20:00')
    
    if enabled and channel_id:
        channel = bot.get_channel(int(channel_id))
        channel_mention = channel.mention if channel else f"<#{channel_id}>"
        status = f"✅ **Aktiv**\n📍 Channel: {channel_mention}\n🕐 Zeit: {time} Uhr"
    else:
        status = "⏹️ **Inaktiv**"
    
    await ctx.send(f"**Fear & Greed Index Scheduler Status:**\n{status}")

@bot.command(name='fng_time')
async def fng_time(ctx, time_str: str = "20:00"):
    """
    Set the time for daily Fear & Greed Index posts
    Usage: !fng_time 20:00
    """
    try:
        # Validate time format
        hour, minute = map(int, time_str.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        
        fng_scheduler_settings['time'] = time_str
        save_scheduler_settings()
        
        # Restart scheduler with new time
        if fear_greed_scheduler.is_running():
            fear_greed_scheduler.cancel()
            # Update the loop time
            fear_greed_scheduler.change_interval(time=dt_time(hour=hour, minute=minute))
            if fng_scheduler_settings.get('enabled', False):
                fear_greed_scheduler.start()
        
        await ctx.send(f"✅ Fear & Greed Index wird jetzt täglich um {time_str} Uhr gepostet!")
    except ValueError:
        await ctx.send("❌ Ungültiges Zeitformat. Verwende HH:MM (z.B. 20:00)")

@bot.command(name='help_summary')
async def help_summary(ctx):
    """Show help for summary commands"""
    help_text = """
📖 **Discord Summary Bot Commands**

**Zusammenfassungen:**
**!updateme #channel1 #channel2**
Get a summary of new messages since your last check for the specified channels.
- Tracks your reading history per channel
- First use: summaries messages from last 24 hours

**!summarize [#channel] [hours]**
Get a full summary of messages from a channel within a time period.
- Default: current channel, last 24 hours
- Example: `!summarize #general 48` (last 48 hours)
- Maximum: 720 hours (30 days)

**Fear & Greed Index:**
**!fng**
Zeigt den aktuellen Fear & Greed Index

**!fng_start [#channel]**
Startet automatische tägliche Posts (Standard: 20:00 Uhr)
- Beispiel: `!fng_start #crypto`

**!fng_stop**
Stoppt automatische Posts

**!fng_status**
Zeigt den Status des Schedulers

**!fng_time HH:MM**
Ändert die Uhrzeit für tägliche Posts
- Beispiel: `!fng_time 18:30`

**!help_summary**
Show this help message
"""
    await ctx.send(help_text)

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument. Use `!help_summary` for usage information.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Invalid argument. Use `!help_summary` for usage information.")
    else:
        await ctx.send(f"❌ An error occurred: {str(error)}")
        print(f"Error: {error}")

# Run the bot
if __name__ == "__main__":
    if not DISCORD_TOKEN or not OPENAI_API_KEY:
        print("Error: Missing DISCORD_TOKEN or OPENAI_API_KEY environment variables")
    else:
        bot.run(DISCORD_TOKEN)