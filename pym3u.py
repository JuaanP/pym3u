from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.card import MDCard
from kivymd.uix.list import ThreeLineAvatarListItem, ImageLeftWidget, MDList
from kivymd.uix.button import MDIconButton
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.textfield import MDTextField
from kivy.uix.scrollview import ScrollView
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.properties import StringProperty, ObjectProperty, NumericProperty
from kivymd.uix.filemanager import MDFileManager
from kivymd.uix.snackbar import Snackbar
import vlc
import threading
import asyncio
import re
import aiohttp
import os
from collections import deque
from kivy.metrics import dp
from functools import partial
from kivy.clock import mainthread

if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class AsyncImageLeftWidget(ImageLeftWidget):
    source = StringProperty()
    
    def update_source(self, new_source):
        self.source = new_source

class ChannelCard(MDCard):
    def __init__(self, channel_id, name, url, on_release_callback, **kwargs):
        super().__init__(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(80),
            padding=dp(10),
            spacing=dp(10),
            ripple_behavior=True,
            radius=[dp(10),],
            elevation=2,
            **kwargs
        )
        self.channel_id = channel_id
        
        self.image = AsyncImageLeftWidget(
            source="default_channel.png",
            size_hint=(None, None),
            size=(dp(60), dp(60))
        )
        
        text_container = MDBoxLayout(
            orientation='vertical',
            padding=(dp(10), 0)
        )
        
        display_name = name.split('|')[-1].strip() if '|' in name else name
        channel_name = MDLabel(
            text=display_name,
            theme_text_color="Primary",
            font_style="Subtitle1",
            bold=True
        )
        
        channel_url = MDLabel(
            text=url[:50] + "..." if len(url) > 50 else url,
            theme_text_color="Secondary",
            font_style="Caption"
        )
        
        text_container.add_widget(channel_name)
        text_container.add_widget(channel_url)
        
        self.add_widget(self.image)
        self.add_widget(text_container)
        
        self.bind(on_release=lambda x: on_release_callback(url))

class LazyScrollView(ScrollView):
    def __init__(self, load_more_callback, **kwargs):
        super().__init__(**kwargs)
        self.load_more_callback = load_more_callback
        self.bind(scroll_y=self.check_scroll)
        self._prev_scroll_y = 1.0
        self._loading = False
        
    def check_scroll(self, instance, value):
        # Detectar dirección del scroll
        scroll_down = value < self._prev_scroll_y
        self._prev_scroll_y = value
        
        # Cargar más cuando nos acercamos al final (20% del final)
        if scroll_down and value <= 0.2 and not self._loading:
            self._loading = True
            self.load_more_callback()
            Clock.schedule_once(lambda dt: setattr(self, '_loading', False), 1)

class ChannelItem(ThreeLineAvatarListItem):
    def __init__(self, text="", secondary_text="", tertiary_text="", channel_logo=None, **kwargs):
        super().__init__(
            text=text,
            secondary_text=secondary_text,
            tertiary_text=tertiary_text,
            **kwargs
        )
        self.avatar = ImageLeftWidget(
            source=channel_logo if channel_logo and os.path.exists(channel_logo) else "default_channel.png"
        )
        self.add_widget(self.avatar)

class PyM3U(MDApp):
    def __init__(self):
        super().__init__()
        Window.size = (800, 600)
        self.player = None
        self.current_playlist = []
        self.filtered_playlist = []  # New list for filtered results
        self.current_index = 0
        self.file_manager = None
        self.visible_batch_size = 15
        self.preload_batch_size = 10
        self.current_load_index = 0
        self.is_loading = False
        self.logo_cache = {}
        self.channel_cards = {}
        self.logo_download_queue = asyncio.Queue()
        
        self.cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        self.loop_thread = threading.Thread(target=self.run_loop, daemon=True)
        self.loop_thread.start()
        
        self.logo_download_task = None

    def run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start_logo_downloader(self):
        if not self.logo_download_task or self.logo_download_task.done():
            self.logo_download_task = asyncio.run_coroutine_threadsafe(
                self.logo_downloader_worker(), self.loop
            )

    async def logo_downloader_worker(self):
        while True:
            try:
                channel_id, logo_url, channel_name = await self.logo_download_queue.get()
                if logo_url:
                    logo_path = await self.download_logo(logo_url, channel_name)
                    if logo_path and channel_id in self.channel_cards:
                        # Actualizar la imagen en el thread principal
                        Clock.schedule_once(
                            lambda dt, cid=channel_id, path=logo_path: self.update_channel_logo(cid, path)
                        )
                self.logo_download_queue.task_done()
            except Exception as e:
                print(f"Error in logo downloader worker: {e}")

    @mainthread
    def update_channel_logo(self, channel_id, logo_path):
        if channel_id in self.channel_cards:
            card = self.channel_cards[channel_id]
            card.image.update_source(logo_path)

    def build(self):
        self.theme_cls.primary_palette = "DeepPurple"
        self.theme_cls.theme_style = "Light"
        
        screen = MDScreen()
        
        main_layout = MDBoxLayout(
            orientation='vertical',
            spacing=dp(10),
            padding=dp(10)
        )
        
        # Barra superior
        top_bar = MDBoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(60),
            spacing=dp(10),
            padding=[dp(10), dp(5), dp(10), dp(5)],
            md_bg_color=self.theme_cls.primary_color
        )
        
        open_button = MDIconButton(
            icon="folder",
            on_release=self.open_file_manager,
            theme_text_color="Custom",
            text_color=[1, 1, 1, 1]
        )
        
        title = MDLabel(
            text="PyM3U",
            halign="center",
            theme_text_color="Custom",
            text_color=[1, 1, 1, 1],
            font_style="H6"
        )
        
        top_bar.add_widget(open_button)
        top_bar.add_widget(title)
        
        # Barra de búsqueda
        search_container = MDBoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(50),
            spacing=dp(10),
            padding=[dp(10), dp(5), dp(10), dp(5)]
        )
        
        self.search_field = MDTextField(
            hint_text="Buscar canales...",
            mode="round",
            icon_left="magnify",
            helper_text="Escribe para filtrar los canales",
            helper_text_mode="on_focus"
        )
        self.search_field.bind(text=self.on_search_text_change)
        
        search_container.add_widget(self.search_field)
        
        # ScrollView con lazy loading
        self.scroll = LazyScrollView(
            self.load_more_channels,
            do_scroll_x=False,
            do_scroll_y=True,
            effect_cls='ScrollEffect',
            bar_width=dp(10)
        )
        
        self.channels_list = MDList(
            spacing=dp(5),
            padding=(dp(5), dp(5))
        )
        
        self.scroll.add_widget(self.channels_list)
        
        # Controles de reproducción
        controls = MDBoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(50),
            spacing=dp(10),
            padding=[dp(10), dp(5), dp(10), dp(5)]
        )
        
        self.prev_button = MDIconButton(
            icon="skip-previous",
            on_release=self.prev_track
        )
        
        self.play_button = MDIconButton(
            icon="play",
            on_release=self.play_pause
        )
        
        self.next_button = MDIconButton(
            icon="skip-next",
            on_release=self.next_track
        )
        
        controls.add_widget(self.prev_button)
        controls.add_widget(self.play_button)
        controls.add_widget(self.next_button)
        
        # Barra de estado
        self.status_bar = MDLabel(
            text="Seleccione una lista M3U",
            size_hint_y=None,
            height=dp(30),
            halign="center",
            theme_text_color="Secondary"
        )
        
        main_layout.add_widget(top_bar)
        main_layout.add_widget(search_container)  # Add search container
        main_layout.add_widget(self.scroll)
        main_layout.add_widget(controls)
        main_layout.add_widget(self.status_bar)
        
        screen.add_widget(main_layout)
        
        return screen
    
    def on_search_text_change(self, instance, value):
        """Filter channels based on search text"""
        Clock.schedule_once(lambda dt: self.filter_channels(value), 0.5)

    def filter_channels(self, search_text):
        """Apply filter to channels and update the display"""
        try:
            # Clear current display
            self.channels_list.clear_widgets()
            self.channel_cards.clear()
            self.current_load_index = 0
            
            # Filter playlist
            search_text = search_text.lower()
            self.filtered_playlist = [
                channel for channel in self.current_playlist
                if search_text in channel.get('name', '').lower()
            ]
            
            # Update status
            if search_text:
                self.status_bar.text = f'Encontrados {len(self.filtered_playlist)} canales'
            else:
                self.status_bar.text = f'Mostrando {len(self.current_playlist)} canales'
            
            # Start loading filtered results
            self.start_channel_loading()
            
        except Exception as e:
            print(f"Error al filtrar canales: {str(e)}")
            self.status_bar.text = f'Error al filtrar: {str(e)}'

    def load_more_channels(self, *args):
        """Modified to work with filtered results"""
        if not self.is_loading:
            playlist_to_use = self.filtered_playlist if self.search_field.text else self.current_playlist
            if self.current_load_index < len(playlist_to_use):
                self.start_channel_loading()

    def open_file_manager(self, *args):
        if not self.file_manager:
            self.file_manager = MDFileManager(
                exit_manager=self.exit_file_manager,
                select_path=self.select_m3u_file,
            )
        self.file_manager.show('/')

    def exit_file_manager(self, *args):
        self.file_manager.close()

    async def download_logo(self, logo_url, channel_name):
        if not logo_url:
            return None
            
        try:
            # Sanitizar el nombre del canal para el archivo
            safe_name = "".join([c for c in channel_name if c.isalnum() or c in (' ', '-', '_')]).rstrip()
            filename = os.path.join(self.cache_dir, f"{safe_name}.png")
            
            # Si ya existe el logo en caché, retornarlo
            if os.path.exists(filename):
                return filename
            
            # Sanitizar la URL del logo
            logo_url = logo_url.strip()
            if not logo_url.startswith(('http://', 'https://')):
                return None
                
            async with aiohttp.ClientSession() as session:
                try:
                    # Aumentar el timeout y agregar headers
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    async with session.get(logo_url, timeout=aiohttp.ClientTimeout(total=10), headers=headers, ssl=False) as response:
                        if response.status == 200:
                            content = await response.read()
                            # Verificar que el contenido sea una imagen
                            if content.startswith(b'\x89PNG') or content.startswith(b'\xFF\xD8\xFF') or content.startswith(b'GIF8'):
                                with open(filename, 'wb') as f:
                                    f.write(content)
                                return filename
                        return None
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    print(f"Error downloading logo from {logo_url}: {str(e)}")
                    return None
                        
        except Exception as e:
            print(f"Error processing logo for {channel_name}: {str(e)}")
            return None

    def parse_m3u_line(self, line):
        info = {}
        try:
            # Optimización del parsing con una sola pasada de regex
            patterns = {
                'logo': r'tvg-logo="([^"]+)"',
                'name': r'tvg-name="([^"]+)"',
                'group': r'group-title="([^"]+)"',
                'id': r'tvg-id="([^"]+)"'
            }
            
            for key, pattern in patterns.items():
                match = re.search(pattern, line)
                if match:
                    info[key] = match.group(1)
            
            # Fallback para nombre si no se encontró con tvg-name
            if 'name' not in info:
                name_match = re.search(r',([^,]+)$', line)
                if name_match:
                    info['name'] = name_match.group(1).strip()
                    
        except Exception as e:
            print(f"Error parsing M3U line: {str(e)}")
            
        return info
    
    async def load_channel_batch(self):
        if self.is_loading:
            return
            
        self.is_loading = True
        try:
            # Use filtered playlist if search is active
            playlist_to_use = self.filtered_playlist if self.search_field.text else self.current_playlist
            
            batch_end = min(
                self.current_load_index + self.visible_batch_size + self.preload_batch_size,
                len(playlist_to_use)
            )
            
            channels_to_add = []
            for i in range(self.current_load_index, batch_end):
                channel = playlist_to_use[i]
                channel_id = f"channel_{i}"
                name = channel.get('name', f'Canal {i+1}')
                url = channel.get('url', '')
                logo_url = channel.get('logo', '')
                
                channels_to_add.append((channel_id, name, url, logo_url))
            
            Clock.schedule_once(
                lambda dt: self.add_channel_batch(channels_to_add)
            )
            
            self.current_load_index = batch_end
            
        except Exception as e:
            print(f"Error en load_channel_batch: {e}")
        finally:
            self.is_loading = False
    
    def add_channel_batch(self, channels):
        try:
            for channel_id, name, url, logo_url in channels:
                card = ChannelCard(
                    channel_id=channel_id,
                    name=name,
                    url=url,
                    on_release_callback=self.play_stream
                )
                
                self.channels_list.add_widget(card)
                self.channel_cards[channel_id] = card
                
                # Agregar logo a la cola de descarga
                asyncio.run_coroutine_threadsafe(
                    self.logo_download_queue.put((channel_id, logo_url, name)),
                    self.loop
                )
                
        except Exception as e:
            print(f"Error al agregar lote de canales: {str(e)}")

    def add_channel_item(self, name, url, logo_path):
        try:
            # Card mejorada para cada canal
            card = MDCard(
                orientation='horizontal',
                size_hint_y=None,
                height=80,
                padding=10,
                spacing=10,
                ripple_behavior=True,
                radius=[10,],
                elevation=2
            )
            
            # Imagen del canal
            image = ImageLeftWidget(
                source=logo_path if logo_path and os.path.exists(logo_path) else "default_channel.png",
                size_hint=(None, None),
                size=(60, 60)
            )
            
            # Contenedor de texto
            text_container = MDBoxLayout(
                orientation='vertical',
                padding=(10, 0)
            )
            
            # Nombre del canal (sin el prefijo CUL |)
            display_name = name.split('|')[-1].strip() if '|' in name else name
            
            # Nombre del canal
            channel_name = MDLabel(
                text=display_name,
                theme_text_color="Primary",
                font_style="Subtitle1",
                bold=True
            )
            
            # URL del canal
            channel_url = MDLabel(
                text=url[:50] + "..." if len(url) > 50 else url,
                theme_text_color="Secondary",
                font_style="Caption"
            )
            
            text_container.add_widget(channel_name)
            text_container.add_widget(channel_url)
            
            card.add_widget(image)
            card.add_widget(text_container)
            
            # Agregar evento de clic
            card.bind(on_release=lambda x: self.play_stream(url))
            
            # Agregar la card a la lista
            self.channels_list.add_widget(card)
            
        except Exception as e:
            print(f"Error al agregar canal: {str(e)}")

    def select_m3u_file(self, path):
        print(f"Archivo seleccionado: {path}")  # Debug
        self.file_manager.close()
        try:
            self.load_playlist(path)
        except Exception as e:
            print(f"Error en select_m3u_file: {str(e)}")  # Debug
            self.status_bar.text = f'Error al seleccionar archivo: {str(e)}'


    def start_channel_loading(self):
        try:
            future = asyncio.run_coroutine_threadsafe(self.load_channel_batch(), self.loop)
            future.add_done_callback(self.on_batch_complete)
        except Exception as e:
            print(f"Error en start_channel_loading: {str(e)}")
            self.status_bar.text = f'Error al cargar canales: {str(e)}'

    def on_batch_complete(self, future):
            try:
                future.result()  # Obtener el resultado o la excepción si ocurrió
            except Exception as e:
                print(f"Error en batch loading: {str(e)}")

    def load_playlist(self, filepath):
        try:
            self.channels_list.clear_widgets()
            self.current_playlist = []
            self.current_load_index = 0
            self.channel_cards.clear()
            
            if not os.path.exists(filepath) or not filepath.lower().endswith('.m3u'):
                self.status_bar.text = 'Error: Archivo no válido'
                return
            
            # Cargar toda la playlist en memoria
            current_info = {}
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#EXTINF:'):
                        current_info = self.parse_m3u_line(line)
                    elif line and not line.startswith('#'):
                        if current_info:
                            current_info['url'] = line
                            self.current_playlist.append(current_info)
                            current_info = {}
            
            if len(self.current_playlist) > 0:
                self.status_bar.text = f'Cargados {len(self.current_playlist)} canales'
                self.start_logo_downloader()
                # Cargar solo el primer lote de canales
                Clock.schedule_once(lambda dt: self.start_channel_loading(), 0)
            else:
                self.status_bar.text = 'No se encontraron canales en el archivo'
                
        except Exception as e:
            print(f"Error al cargar playlist: {str(e)}")
            self.status_bar.text = f'Error al cargar playlist: {str(e)}'

    def on_stop(self):
            # Limpiar recursos al cerrar la aplicación
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            if hasattr(self, 'loop_thread'):
                self.loop_thread.join(timeout=1)

    def play_stream(self, url):
        try:
            if self.player:
                self.player.stop()
            
            instance = vlc.Instance()
            self.player = instance.media_player_new()
            media = instance.media_new(url)
            self.player.set_media(media)
            self.player.play()
            
            self.play_button.icon = "pause"
            self.status_bar.text = f'Reproduciendo: {url}'
            
        except Exception as e:
            self.status_bar.text = f'Error al reproducir: {str(e)}'

    def play_pause(self, instance):
        if self.player:
            if self.player.is_playing():
                self.player.pause()
                self.play_button.icon = "play"
            else:
                self.player.play()
                self.play_button.icon = "pause"

    def prev_track(self, instance):
        if self.current_index > 0:
            self.current_index -= 1
            self.play_stream(self.current_playlist[self.current_index]['url'])

    def next_track(self, instance):
        if self.current_index < len(self.current_playlist) - 1:
            self.current_index += 1
            self.play_stream(self.current_playlist[self.current_index]['url'])

if __name__ == '__main__':
    def run_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    loop = asyncio.new_event_loop()
    threading.Thread(target=run_loop, args=(loop,), daemon=True).start()
    
    PyM3U().run()