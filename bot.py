import nextcord
from nextcord import Interaction, SlashOption
from nextcord.ext import commands
from nextcord import Game
import yt_dlp
import asyncio
from collections import deque
import re
from googleapiclient.discovery import build
import validators
import json
import colorama
from datetime import datetime
import logging 
from asyncio import Semaphore

colorama.init(autoreset=True)

# Configure logging
logging.basicConfig(
    filename='bot.log',  # Specify the log file
    level=logging.INFO,  # Set the logging level
    format='%(asctime)s [%(levelname)s] - %(message)s',
)

# Load configuration from config.json
with open('config.json', 'r') as config_file:
    config_data = json.load(config_file)

class Bot(commands.Bot):
    def __init__(self, command_prefix, intents):
        super().__init__(command_prefix=command_prefix, intents=intents)
        self.YOUTUBE_API_KEY = config_data.get('youtube_api_key')
        self.youtube = build('youtube', 'v3', developerKey=self.YOUTUBE_API_KEY)
        self.ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn',
        }
        self.server_data = {}  # Store server-specific data
        self.is_playing_dict = {}  # Dictionary to track whether the bot is playing in each server
        self.start_time = datetime.now()
        
    async def on_ready(self):
        logging.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        logging.info(f'Connected to {len(self.guilds)} guilds:')
        logging.info('+' + '-' * 42 + '+')

        for guild in self.guilds:
            guild_id = guild.id
            print(colorama.Fore.GREEN + f'| Guild: {guild.name.ljust(30)} |')
            print(colorama.Fore.GREEN + f'| ID: {str(guild_id).ljust(30)} |')
            print(colorama.Fore.GREEN + f'| Members: {str(guild.member_count).ljust(26)} |')
            print(colorama.Fore.BLUE + '+' + '-'*42 + '+')

            # Initialize server-specific data for each guild
            if guild_id not in self.server_data:
                self.server_data[guild_id] = {'queue': deque(), 'is_playing': False}

            # Initialize is_playing_dict for each guild
            if guild_id not in self.is_playing_dict:
                self.is_playing_dict[guild_id] = False

        print(colorama.Fore.GREEN + f'Total Guild Count: {len(self.guilds)}')
        print(colorama.Fore.GREEN + f'Invite link: https://discord.com/oauth2/authorize?client_id={self.user.id}&scope=bot&permissions=36719616')

        # Set presence when the bot is ready
        await self.change_presence(activity=Game(name='MULTISERVER SUPPORT'))

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandError):
            logging.error(f"Command '{ctx.command.name}' failed: {error}")
        elif isinstance(error, commands.MissingRequiredArgument):
            logging.error(f"Missing required argument for command '{ctx.command.name}': {error}")
        elif isinstance(error, commands.BadArgument):
            logging.error(f"Bad argument for command '{ctx.command.name}': {error}")
        else:
            logging.error(f"An error occurred: {error}")

        
    async def get_playlist_info(self, playlist_url):
        playlist_id_match = re.search(r"list=([a-zA-Z0-9_-]+)", playlist_url)
        if playlist_id_match:
            playlist_id = playlist_id_match.group(1)

            # Request to get playlist details
            playlist_request = self.youtube.playlists().list(part="snippet", id=playlist_id)
            try:
                playlist_response = playlist_request.execute()

                if 'items' in playlist_response and playlist_response['items']:
                    playlist_info = playlist_response['items'][0]['snippet']
                    playlist_title = playlist_info['title']

                    # Request to get playlist items
                    items_request = self.youtube.playlistItems().list(part="contentDetails", playlistId=playlist_id, maxResults=50)
                    items_response = items_request.execute()

                    if 'items' in items_response and items_response['items']:
                        playlist_urls = [f"https://www.youtube.com/watch?v={item['contentDetails']['videoId']}" for item in items_response['items']]
                        return {'title': playlist_title, 'videos': playlist_urls}
                    else:
                        return None
                else:
                    return None
            except Exception as e:
                print(f"Error fetching playlist information: {e}")
                return None

        return None

    async def search_music(self, query):
        try:
            # Call the YouTube API to search for videos
            search_response = self.youtube.search().list(
                q=query,
                part='id,snippet',
                maxResults=5,  # You can adjust this number based on your preference
                type='video'
            ).execute()

            # Extract video information from the search results
            search_results = []
            for result in search_response.get('items', []):
                video_id = result['id']['videoId']
                video_url = f'https://www.youtube.com/watch?v={video_id}'
                video_title = result['snippet']['title']
                search_results.append({'title': video_title, 'url': video_url})

            return search_results

        except Exception as e:
            print(f"Error in search_music: {e}")
            return []

    def get_youtube_video_id(self, url):
        # Extract video ID from YouTube URL
        match = re.search(r"(?<=v=)[a-zA-Z0-9_-]+", url)
        if match:
            return match.group(0)
        else:
            return None
            
    async def on_voice_state_update(self, member, before, after):
        # Check if the bot is connected to the correct voice channel
        guild_id = member.guild.id
        if self.voice_clients and member.guild == self.voice_clients[0].guild and not after.channel:
            try:
                # Reconnect to the voice channel and play the next song
                asyncio.create_task(self.reconnect_to_voice_channel(guild_id))
                await self.play_next_song(guild_id)
            except Exception as e:
                print(f"Error in on_voice_state_update: {e}")

        # Handle disconnects
        if before.channel and not after.channel:
            # Reconnect if the bot was disconnected from the voice channel
            asyncio.create_task(self.reconnect_to_voice_channel(guild_id))

    async def send_embed_message(self, interaction, title, description, color):
        embed = nextcord.Embed(title=title, description=description, color=color)

        # Add author field with username and avatar of the user who triggered the command
        user = interaction.user
        embed.set_author(name=user.name, icon_url=user.avatar.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_song_end(self, guild_id):
        self.is_playing_dict[guild_id] = False
        # Use await when calling play_next_song
        await self.play_next_song(self.voice_clients[0], guild_id)
      
    async def get_video_info_playlist(self, url):
        # Check if the input is a valid URL
        if not validators.url(url):
            # If it's not a URL, treat it as a search query
            search_results = await self.get_search_results(url)
            if search_results:
                # Return the first search result
                return search_results[0]
            else:
                return None

        # Use yt_dlp to extract playlist information
        ydl = yt_dlp.YoutubeDL({'quiet': True})
        playlist_info = ydl.extract_info(url, download=False)

        if 'entries' in playlist_info:
            # If it's a playlist, return the information for the first video
            return playlist_info['entries'][0]
        else:
            return None

    async def get_video_title(self, video_id_or_url):
        try:
            info_dict = await self.get_video_info_single(video_id_or_url)
            return info_dict.get('title', 'Unknown Title')
        except Exception as e:
            print(f"Error in get_video_title: {e}")
            return 'Unknown Title'

    async def get_search_results(self, query):
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': "-",
            'quiet': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(query, download=False)

            entries = search_results.get('entries', [])

            if entries:
                # Return a list of dictionaries containing URL and title for each video in the search results
                results = [{'url': entry['formats'][0]['url'], 'title': entry['title']} for entry in entries]
                return results
            else:
                return []

    async def get_video_info_youtube_api(self, video_id):
        # Use self.youtube instead of youtube
        video_request = self.youtube.videos().list(part="snippet", id=video_id)
        try:
            video_response = video_request.execute()

            if 'items' in video_response and video_response['items']:
                return video_response['items'][0]['snippet']
            else:
                return None
        except Exception as e:
            print(f"Error in get_video_info_youtube_api: {e}")
            return None

    async def get_current_song_title(self):
        # Check if there is a voice client and it is playing
        if self.voice_clients:
            voice_client = self.voice_clients[0]  # Assuming there is only one voice client

            # Get the title of the currently playing song
            current_song_title = "Unknown"
            if voice_client.is_playing():
                # Extract the info of the currently playing song
                ydl = yt_dlp.YoutubeDL({'format': 'bestaudio/best', 'outtmpl': "-", 'quiet': True})
                info_dict = ydl.extract_info(self.queue[0], download=False)
                current_song_title = info_dict.get('title', 'Unknown')

            return current_song_title
        else:
            return "No song is currently playing."

    async def on_song_end(self, guild_id):
        self.server_data[guild_id]['is_playing'] = False
        await self.play_next_song(guild_id)

    async def get_voice_client(self, guild_id):
        voice_channel = next((vc.channel for vc in self.voice_clients if vc.guild.id == guild_id), None)

        if voice_channel:
            # Check if the bot is already connected to the correct voice channel
            if self.voice_clients and voice_channel == self.voice_clients[0].channel:
                return self.voice_clients[0]
            else:
                try:
                    # Bot is not in the correct voice channel, reconnect
                    voice_client = await voice_channel.connect()
                    await voice_client.guild.change_voice_state(channel=voice_channel, self_deaf=True)
                    return voice_client
                except nextcord.errors.ClientException:
                    # Bot is already connected to a voice channel
                    print("Bot is already connected to a voice channel. Ignoring the reconnection.")
                    return None
        else:
            print("Voice channel not found. Ignoring playback.")
            return None
            
    async def play_next_song(self, guild_id):
        async with operation_semaphore:
            if not self.voice_clients or not self.voice_clients[0].is_connected():
                # If not connected, attempt to reconnect
                await self.reconnect_to_voice_channel(guild_id)
                # Return to avoid further execution until reconnection is complete
                return

            # Get the voice client for the current guild
            voice_client = next((vc for vc in self.voice_clients if vc.guild.id == guild_id), None)

            if not voice_client:
                print("Bot is not in the correct voice channel. Ignoring the playback.")
                return  # Return if not in the correct voice channel

            if voice_client.is_playing():
                # Bot is already playing, don't start another song
                return

            while self.server_data[guild_id]['queue']:
                # Use deque's popleft() instead of list's pop(0)
                next_song_url = self.server_data[guild_id]['queue'].popleft()

                if re.match(r'https?://', next_song_url):
                    info_dict = await self.get_video_info_single(next_song_url)
                else:
                    info_dict = await self.get_video_info_playlist(next_song_url)

                try:
                    if info_dict and 'formats' in info_dict and info_dict['formats']:
                        audio_url = info_dict['formats'][0]['url']
                        source = self.YTDLSource(info_dict, self.ffmpeg_options)  # Pass ffmpeg_options

                        # Check if the bot is still connected to the correct voice channel
                        if voice_client and voice_client.is_connected():
                            # Play the song
                            voice_client.play(source.create_ffmpeg_player(), after=lambda e: self.on_song_end(guild_id))

                            # Update the is_playing status for the specific guild
                            self.is_playing_dict[guild_id] = True

                            while voice_client.is_playing():
                                await asyncio.sleep(1)

                        else:
                            print("Bot is not in the correct voice channel. Ignoring the playback.")
                            break  # Break the loop if not in the correct voice channel
                    else:
                        print("No valid audio URL found. Ignoring the playback.")
                except yt_dlp.utils.ExtractorError as e:
                    await self.handle_song_play_error(e, voice_client, guild_id)
                except yt_dlp.utils.DownloadError as e:
                    await self.handle_song_play_error(e, voice_client, guild_id)

            # Use await when calling play_next_song
            await self.reconnect_to_voice_channel(guild_id)


    async def handle_song_play_error(self, error, voice_client, guild_id):
        error_message = str(error)
        print(f"Error during song playback: {error_message}")

        if "Video unavailable" in error_message:
            print("Video unavailable. Skipping to the next song.")
        elif "This video requires payment to watch" in error_message:
            print("This video requires payment to watch. Skipping to the next song.")
        elif "This video is age-restricted" in error_message:
            print("This video is age-restricted. Skipping to the next song.")
        elif "Unable to extract video data" in error_message:
            print("Unable to extract video data. Skipping to the next song.")
        else:
            print("Unknown error. Skipping to the next song.")

        # If there's an error, play the next song
        await self.play_next_song(voice_client, guild_id)

    async def play_song(self, voice_client, song_url, guild_id):
        global is_playing_dict

        info_dict = self.get_video_info(song_url)

        try:
            if info_dict and 'formats' in info_dict and info_dict['formats']:
                audio_url = info_dict['formats'][0]['url']
                source = self.YTDLSource(info_dict)
                voice_client.play(source.create_ffmpeg_player(), after=lambda e: self.on_song_end(guild_id))
                is_playing_dict[guild_id] = True

                while voice_client.is_playing():
                    await asyncio.sleep(1)

                # Use await when calling play_next_song
                await self.play_next_song(voice_client, guild_id)
            else:
                print("No audio formats found for the provided URL.")
        except yt_dlp.utils.ExtractorError as e:
            await self.handle_song_play_error(e, voice_client, guild_id)
        except yt_dlp.utils.DownloadError as e:
            await self.handle_song_play_error(e, voice_client, guild_id)

    async def get_video_info_single(self, url):
        ydl = yt_dlp.YoutubeDL({'format': 'bestaudio/best', 'outtmpl': "-", 'quiet': True})
        info_dict = ydl.extract_info(url, download=False)

        if 'formats' in info_dict and info_dict['formats']:
            return info_dict
        else:
            return None

    async def get_current_song_title(self, guild_id):
        # Check if there is a voice client and it is playing
        if self.voice_clients:
            voice_client = next((vc for vc in self.voice_clients if vc.guild.id == guild_id), None)

            # Get the title of the currently playing song
            current_song_title = "Unknown"
            if voice_client and voice_client.is_playing() and guild_id in self.server_data and 'queue' in self.server_data[guild_id]:
                # Extract the info of the currently playing song
                ydl = yt_dlp.YoutubeDL({'format': 'bestaudio/best', 'outtmpl': "-", 'quiet': True})
                info_dict = ydl.extract_info(self.server_data[guild_id]['queue'][0], download=False)
                current_song_title = info_dict.get('title', 'Unknown')

            return current_song_title
        else:
            return "No song is currently playing."

    async def get_queue_embed(self, guild_id, user):
        queue_embed = nextcord.Embed(
            title=f"Queue for {self.get_guild(guild_id).name}",
            color=0x3498db
        )

        if guild_id in self.server_data and 'queue' in self.server_data[guild_id]:
            queue = self.server_data[guild_id]['queue']
            max_fields = 24

            # Inside the for loop where fields are added to the embed
            queue_list = []
            for i, song_url in enumerate(queue, start=1):
                if i <= max_fields:
                    video_id = self.get_youtube_video_id(song_url)
                    if video_id:
                        info_dict = await self.get_video_info_youtube_api(video_id)
                        title = info_dict.get('title', 'Unknown')
                        queue_list.append(f"{i}. [{title}]({song_url})")
                    else:
                        queue_list.append(f"{i}. Unknown Video - [{song_url}]({song_url})")
                else:
                    remaining_songs = len(queue) - max_fields
                    queue_list.append(f"... and {remaining_songs} more. Use `/queue` to view the complete queue.")
                    break

            # Combine the queue list into a string and set it as the description
            queue_embed.description = "\n".join(queue_list)

        else:
            queue_embed.description = "The queue is currently empty."

        # Set the author of the embed
        queue_embed.set_author(name=user.name, icon_url=user.avatar.url)

        return queue_embed

    async def add_to_queue(self, guild_id, song_url):
        if guild_id in self.server_data and 'queue' in self.server_data[guild_id]:
            self.server_data[guild_id]['queue'].append(song_url)
            return True
        else:
            return False
            
    async def play(self, ctx, url: str = None):
        user = ctx.user
        guild_id = ctx.guild.id

        if not user.voice or not user.voice.channel:
            await ctx.send("You need to be in a voice channel to use this command.")
            return

        # Check if the provided input is a valid URL
        if not url or not validators.url(url):
            await ctx.send("Please provide a valid URL.")
            return

        voice_channel = user.voice.channel

        if guild_id not in self.server_data:
            self.server_data[guild_id] = {'queue': deque(), 'is_playing': False}
            self.is_playing_dict[guild_id] = False

        # Check if the bot is already connected to a voice channel
        if not user.guild.voice_client:
            try:
                voice_client = await asyncio.wait_for(voice_channel.connect(), timeout=120)

                # Deafen the bot when it joins
                await voice_client.guild.change_voice_state(channel=voice_channel, self_deaf=True)

                playlist_info = await bot.get_playlist_info(url)

                if playlist_info:
                    # Playlist detected
                    playlist_urls = playlist_info['videos']
                    self.server_data[guild_id]['queue'] = deque(self.server_data[guild_id]['queue']) + deque(playlist_urls)

                    title = "Added to Queue"
                    description = f"{len(playlist_urls)} songs added from [{playlist_info['title']}]({url})"
                else:
                    # Single song provided
                    self.server_data[guild_id]['queue'] = deque(self.server_data[guild_id]['queue'])
                    self.server_data[guild_id]['queue'].appendleft(url)

                    title = "Added to Queue"
                    description = f"[{await self.get_video_title(self.server_data[guild_id]['queue'][0])}]({url})"

                embed = nextcord.Embed(title=title, description=description, color=0x00ff00)
                embed.set_author(name=user.name, icon_url=user.avatar.url)

                await ctx.send(embed=embed)

                if not self.is_playing_dict[guild_id] and self.server_data[guild_id]['queue']:
                    await self.play_next_song(guild_id)

            except asyncio.TimeoutError:
                await ctx.send("Unable to connect to the voice channel. Connection timed out.")
        else:
            # Bot is already connected, add the song or playlist to the queue
            playlist_info = await self.get_playlist_info(url)

            if playlist_info:
                playlist_urls = playlist_info['videos']
                self.server_data[guild_id]['queue'] = deque(self.server_data[guild_id]['queue']) + deque(playlist_urls)

                title = "Added to Queue"
                description = f"{len(playlist_urls)} songs added from [{playlist_info['title']}]({url})"
            else:
                self.server_data[guild_id]['queue'] = deque(self.server_data[guild_id]['queue'])
                self.server_data[guild_id]['queue'].appendleft(url)

                title = "Added to Queue"
                description = f"[{await self.get_video_title(self.server_data[guild_id]['queue'][0])}]({url})"

            embed = nextcord.Embed(title=title, description=description, color=0x00ff00)
            embed.set_author(name=user.name, icon_url=user.avatar.url)

            await ctx.send(embed=embed)

            if not self.is_playing_dict[guild_id] and self.server_data[guild_id]['queue']:
                await self.play_next_song(guild_id)
               
    async def reconnect_to_voice_channel(self, guild_id, retry_count=0):
        # Get the voice channel for the guild
        voice_channel = next((vc.channel for vc in self.voice_clients if vc.guild.id == guild_id), None)

        if voice_channel:
            # Check if the bot is already connected to the correct voice channel
            if self.voice_clients and voice_channel == self.voice_clients[0].channel:
                # Update the is_playing_dict
                self.is_playing_dict[guild_id] = False

                # Check if there are songs in the queue
                if self.server_data[guild_id]['queue']:
                    # Play the next song
                    await self.play_next_song(guild_id)
            else:
                try:
                    # Bot is not in the correct voice channel, reconnect
                    if self.voice_clients:
                        # Disconnect from the current voice channel
                        await self.voice_clients[0].disconnect()

                    # Exponential backoff with a maximum delay of 60 seconds
                    delay = min(2**retry_count, 600)
                    await asyncio.sleep(delay)

                    # Connect to the correct voice channel
                    voice_client = await voice_channel.connect()
                    await voice_client.guild.change_voice_state(channel=voice_channel, self_deaf=True)

                    # Store the voice client in server_data
                    self.server_data[guild_id]['voice_client'] = voice_client

                    # Update the is_playing_dict
                    self.is_playing_dict[guild_id] = False

                    # Check if there are songs in the queue
                    if self.server_data[guild_id]['queue']:
                        # Check if the bot was playing before the disconnection
                        if self.server_data[guild_id]['is_playing']:
                            # Resume playback
                            voice_client.resume()
                        else:
                            # Play the next song
                            await self.play_next_song(guild_id)
                except nextcord.errors.ClientException as e:
                    print(f"Error reconnecting to voice channel: {e}")
                    # Retry with an increased count
                    await self.reconnect_to_voice_channel(guild_id, retry_count + 1)

    class YTDLSource:
        def __init__(self, data, ffmpeg_options):
            self.data = data
            self.url = data.get('url')
            self.ffmpeg_options = ffmpeg_options  # Assign ffmpeg_options from the Bot class
            self.player = None

        def create_ffmpeg_player(self):
            return nextcord.FFmpegPCMAudio(self.url, **self.ffmpeg_options)

        async def pause(self):
            if self.player:
                self.player.pause()

        async def resume(self):
            if self.player:
                self.player.resume()

# Your bot setup code
intents = nextcord.Intents.default()
intents.message_content = True
bot = Bot(command_prefix='!', intents=intents)
start_time = datetime.now()
operation_semaphore = asyncio.Semaphore(100)  # Adjust the value as needed

@bot.slash_command(name="play", description="Add A Playlist, Mix, Or Single Vid By URL")
async def play(ctx, url: str = SlashOption(description="Single Videos, Playlists & Mixes *NO SEARCHING*")):
    await bot.play(ctx, url)
            
@bot.slash_command(name="stop", description="Stop playback and disconnect from the voice channel")
async def stop(ctx):
    guild_id = ctx.guild.id

    # Get the voice client for the current guild
    voice_client = next((vc for vc in bot.voice_clients if vc.guild.id == guild_id), None)

    if voice_client:
        # Stop playback
        voice_client.stop()

        # Clear the queue for the specific guild
        if guild_id in bot.server_data and 'queue' in bot.server_data[guild_id]:
            bot.server_data[guild_id]['queue'] = []

        # Disconnect from the voice channel
        await voice_client.disconnect()

        # Update is_playing_dict
        bot.is_playing_dict[guild_id] = False

        # Send embed message with username and avatar of the user who triggered the command
        title = "Playback Stopped"
        description = "Playback stopped, and the bot has been disconnected."
        color = 0xff0000  # Red color
        await bot.send_embed_message(ctx, title, description, color)
    else:
        await ctx.send("The bot is not currently connected to a voice channel.")

@bot.slash_command(name="skip", description="Skip the current song or a specified number of songs in the queue")
async def skip(
    ctx,
    num_songs: int = SlashOption(description="Number of songs to skip (default is 1)", required=False)
):

    guild_id = ctx.guild.id

    # Get the voice client for the current guild
    voice_client = next((vc for vc in bot.voice_clients if vc.guild.id == guild_id), None)

    if not voice_client or not voice_client.is_playing():
        await ctx.send("There are no songs currently playing.")
        return

    # If num_songs is None or not provided, set it to the default value of 1
    num_songs = num_songs or 1

    # Ensure the number of songs to skip is positive
    num_songs = max(1, num_songs)

    # Stop the current playback
    voice_client.stop()

    # Access the queue for the specific guild using bot instead of self
    for _ in range(num_songs - 1):  # Skip one less, as we've already stopped one song
        if bot.server_data[guild_id]['queue']:
            bot.server_data[guild_id]['queue'].popleft()
        else:
            break  # Stop if the queue is empty

    # Send an embed message with username and avatar of the user who triggered the command
    title = "Song(s) Skipped"

    # Use ctx.user instead of ctx.author
    description = f"{ctx.user.mention} skipped {num_songs} {'song' if num_songs == 1 else 'songs'}."
    color = 0x00ff00  # Green color
    await bot.send_embed_message(ctx, title, description, color)

@bot.slash_command(name="queue", description="Display the current queue")
async def show_queue(ctx):
    guild_id = ctx.guild.id
    user = ctx.user

    # Check if the guild has a queue
    if guild_id in bot.server_data and 'queue' in bot.server_data[guild_id]:
        queue_embed = await bot.get_queue_embed(guild_id, user)
        await ctx.send(embed=queue_embed)
    else:
        await ctx.send("There is no queue for this server.")

@bot.slash_command(name="reportanerror", description="Report an error or issue to the bot owner.")
async def report_an_error(ctx, message: str = nextcord.SlashOption(description="Enter your error report here")):
    # Replace 'YOUR_USER_ID' with your actual Discord user ID
    owner_user_id = 820062277842632744

    try:
        # Fetch the bot owner using the user ID
        owner = await bot.fetch_user(owner_user_id)

        if not owner:
            await ctx.send("Error: Bot owner not found.")
            return

        # Create an embed for the error report
        embed = nextcord.Embed(
            title="Error Report",
            description=f"**From:** {ctx.user.mention}\n**User ID:** {ctx.user.id}\n\n{message}",
            color=0xff0000  # Red color
        )

        # Send the error report in a direct message to the bot owner
        await owner.send(embed=embed)

        # Create a nice embed for the confirmation message
        confirmation_embed = nextcord.Embed(
            title="Error Report Sent",
            description="Your error report has been successfully sent to the bot owner. They will review your message.",
            color=0x00ff00  # Green color
        )
        confirmation_embed.set_author(name=ctx.user.name, icon_url=ctx.user.avatar.url)

        # Send the confirmation message with the embed
        await ctx.send(embed=confirmation_embed)
    except nextcord.errors.Forbidden:
        # Create an embed for the error message
        error_embed = nextcord.Embed(
            title="Error",
            description="Unable to send a direct message to the bot owner. Make sure their DMs are open.",
            color=0xff0000  # Red color
        )
        error_embed.set_author(name=ctx.user.name, icon_url=ctx.user.avatar.url)

        # Send the error message with the embed
        await ctx.send(embed=error_embed)
    except Exception as e:
        print(f"An error occurred while processing the report_an_error command: {e}")

        # Create an embed for the error message
        error_embed = nextcord.Embed(
            title="Error",
            description="An unexpected error occurred. Please try again later.",
            color=0xff0000  # Red color
        )
        error_embed.set_author(name=ctx.user.name, icon_url=ctx.user.avatar.url)

        # Send the error message with the embed
        await ctx.send(embed=error_embed)

@bot.slash_command(name="stats", description="Show bot uptime")
async def uptime(ctx):
    current_time = datetime.now()
    uptime_duration = current_time - start_time
    days, hours, minutes, seconds = uptime_duration.days, uptime_duration.seconds // 3600, (uptime_duration.seconds // 60) % 60, uptime_duration.seconds % 60

    # Format the date and uptime for display
    start_time_str = start_time.strftime("`%A, %B %d, %Y`")  # Updated date format
    uptime_str = f"`{days}d {hours}h {minutes}m {seconds}s`"  # Simplified uptime format

    # Get the number of servers the bot is in
    server_count = len(bot.guilds)

    # Create an embed with bot information
    embed = nextcord.Embed(
        title="Bot Stats",
        description=f"🚀 **Started on:** {start_time_str}\n⏰ **Uptime:** {uptime_str}\n🌐 **Servers:** `{server_count}`",
        color=0x3498db  # You can set the color based on your preference
    )

    # Add a timestamp to the embed
    embed.timestamp = datetime.utcnow()

    # Set the author of the embed
    embed.set_author(name=bot.user.name, icon_url=bot.user.avatar.url)

    # Send the embed as a response
    await ctx.send(embed=embed)


@bot.slash_command(name="help", description="Show help for commands")
async def help_command(ctx):
    # Create an embed for the help message
    embed = nextcord.Embed(
        title="Bot Commands",
        description="Here are all available commands and their usage:",
        color=0x3498db  # You can set the color based on your preference
    )

    # Add individual fields for each command
    embed.add_field(
        name="play",
        value="Add a playlist, mix, or single video by URL.\nUsage: `/play [URL]`",
        inline=False
    )
    embed.add_field(
        name="stop",
        value="Stop playback and disconnect from the voice channel.\nUsage: `/stop`",
        inline=False
    )
    embed.add_field(
        name="skip",
        value="Skip the current song or a specified number of songs in the queue.\nUsage: `/skip [num_songs]`",
        inline=False
    )
    embed.add_field(
        name="queue",
        value="Display the current queue.\nUsage: `/queue`",
        inline=False
    )
    embed.add_field(
        name="reportanerror",
        value="Report an error or issue to the bot owner.\nUsage: `/reportanerror [message]`",
        inline=False
    )
    embed.add_field(
        name="stats",
        value="Show bot uptime.\nUsage: `/stats`",
        inline=False
    )

    # Send the embed as a response
    await ctx.send(embed=embed)
   
bot.run(config_data.get('token'))
