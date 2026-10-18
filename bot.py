"""
bot.py - Discord bot frontend for Nebfuscator Deobfuscator.
Upload an obfuscated script, get the deobfuscated file back.
"""

import os
import sys
import json
import asyncio
import subprocess
import tempfile
import shutil

# Try to import discord
try:
    import discord
    from discord.ext import commands
except ImportError:
    discord = None
    commands = None

# Add deobf directory to path
DEOBF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deobf")
if DEOBF_DIR not in sys.path:
    sys.path.insert(0, DEOBF_DIR)

import deobf

# Bot configuration
BOT_TOKEN = os.environ.get("NEBULA_BOT_TOKEN", "")
COMMAND_PREFIX = "!"

if discord:
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
else:
    bot = None

def run_deobfuscation(input_path, options=None):
    """Run deobfuscation on a file."""
    args = type('Args', (), {})()
    args.input = input_path
    args.output = None
    args.obfuscator = None
    args.no_devirt = False
    args.devirt = False
    args.debug = False
    args.pypy = False
    args.no_pypy = False
    args.api_dir = None
    args.api_auto = True
    args.max_instructions = 250000
    args.max_block = 25000
    args.max_waits = 50
    args.time_budget = 30
    args.executor = "Wave"
    args.cfg = []
    args.strings = False
    args.keep_harness = False
    args.input_text = None
    args.keep_preamble = False
    args.raw = None
    args.studio = False
    args.port = 34889
    args.studio_wait = 600
    args.raw = None
    
    try:
        result = deobf.deobfuscate(input_path, args)
        return result
    except Exception as e:
        return str(e)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')

@bot.command(name='deobfuscate', help='Deobfuscate a Roblox Luau script')
async def deobfuscate(ctx):
    if not ctx.message.attachments:
        await ctx.send("Please attach a Lua script file to deobfuscate.")
        return
    
    attachment = ctx.message.attachments[0]
    if not attachment.filename.endswith(('.lua', '.luau')):
        await ctx.send("Please upload a Lua (.lua) or Luau (.luau) file.")
        return
    
    # Download the file
    temp_dir = tempfile.mkdtemp(prefix="neb_deobf_")
    input_path = os.path.join(temp_dir, attachment.filename)
    await attachment.save(input_path)
    
    try:
        # Deobfuscate
        result = run_deobfuscation(input_path)
        
        if isinstance(result, str) and result.startswith("!"):
            await ctx.send(f"❌ Deobfuscation failed: {result}")
            return
        
        # Send the deobfuscated file back
        with open(result, 'r', encoding='utf-8') as f:
            content = f.read()
        
        output_filename = os.path.basename(input_path).replace('.lua', '_deobfuscated.lua').replace('.luau', '_deobfuscated.lua')
        output_path = os.path.join(temp_dir, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        await ctx.send(file=discord.File(output_path, filename=output_filename))
        
    except Exception as e:
        await ctx.send(f"❌ Error during deobfuscation: {str(e)}")
    finally:
        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

@bot.command(name='detect', help='Detect obfuscator type')
async def detect(ctx):
    if not ctx.message.attachments:
        await ctx.send("Please attach a Lua script file to detect.")
        return
    
    attachment = ctx.message.attachments[0]
    if not attachment.filename.endswith(('.lua', '.luau')):
        await ctx.send("Please upload a Lua (.lua) or Luau (.luau) file.")
        return
    
    temp_dir = tempfile.mkdtemp(prefix="neb_detect_")
    input_path = os.path.join(temp_dir, attachment.filename)
    await attachment.save(input_path)
    
    try:
        from obfuscators import detect_obfuscator
        with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
            source = f.read()
        
        plugin, conf = detect_obfuscator(source)
        await ctx.send(f"🔍 Detected obfuscator: {plugin.label if plugin else 'unknown'} (confidence: {conf:.2f})")
    except Exception as e:
        await ctx.send(f"❌ Detection failed: {str(e)}")
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

@bot.command(name='help', help='Show available commands')
async def help(ctx):
    embed = discord.Embed(title="Nebfuscator Deobfuscator Bot", description="Deobfuscate Roblox Luau scripts", color=0x00ff00)
    embed.add_field(name="!deobfuscate", value="Upload a Lua/Luau file to deobfuscate it", inline=False)
    embed.add_field(name="!detect", value="Upload a Lua/Luau file to detect obfuscator type", inline=False)
    embed.add_field(name="!help", value="Show this help message", inline=False)
    await ctx.send(embed=embed)

def main():
    if not BOT_TOKEN:
        print("Error: NEBULA_BOT_TOKEN environment variable not set")
        print("Please set your Discord bot token as NEBULA_BOT_TOKEN environment variable")
        return
    
    if not discord:
        print("Error: discord.py not installed")
        print("Install with: pip install discord.py")
        return
    
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()