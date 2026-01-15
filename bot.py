import discord
from discord.ext import commands
import openai
import os
from datetime import datetime, timedelta
import json
from collections import defaultdict
from dotenv import load_dotenv

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
    
    prompt = f"""Please provide a {'comprehensive' if summary_type == 'full' else 'concise'} summary of the following Discord channel messages. 
    
Organize the summary by main topics discussed and key points. Include:
- Main topics and discussions
- Important decisions or announcements
- Action items or questions raised
- Notable attachments or links shared

Messages:
{message_text}

Summary:"""
    
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that summarizes Discord conversations clearly and concisely."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating summary: {str(e)}"

@bot.event
async def on_ready():
    """Bot startup event"""
    load_user_data()
    print(f'{bot.user} has connected to Discord!')
    print(f'Bot is in {len(bot.guilds)} guild(s)')

@bot.command(name='updateme')
async def update_me(ctx, *channels: discord.TextChannel):
    """
    Get a summary of new messages since your last check for specified channels.
    Usage: !updateme #channel1 #channel2
    """
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
    
    if len(full_response) > 2000:
        # Split into multiple messages
        chunks = [full_response[i:i+1900] for i in range(0, len(full_response), 1900)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(full_response)

@bot.command(name='summarize')
async def summarize(ctx, channel: discord.TextChannel = None, hours: int = 24):
    """
    Get a full summary of messages from a channel within a time period.
    Usage: !summarize #channel [hours]
    Example: !summarize #general 48
    """
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
    
    if len(response) > 2000:
        # Split into multiple messages
        chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
        for chunk in chunks:
            await ctx.send(chunk)
    else:
        await ctx.send(response)

@bot.command(name='help_summary')
async def help_summary(ctx):
    """Show help for summary commands"""
    help_text = """
📖 **Discord Summary Bot Commands**

**!updateme #channel1 #channel2**
Get a summary of new messages since your last check for the specified channels.
- Tracks your reading history per channel
- First use: summaries messages from last 24 hours

**!summarize [#channel] [hours]**
Get a full summary of messages from a channel within a time period.
- Default: current channel, last 24 hours
- Example: `!summarize #general 48` (last 48 hours)
- Maximum: 720 hours (30 days)

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
