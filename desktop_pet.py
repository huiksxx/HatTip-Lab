"""Windows transparent desktop pet entrypoint."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import threading
import uuid
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from agent_providers import (
    HermesProvider,
    HttpProvider,
    MockProvider,
    OpenAIResponsesProvider,
    ProviderRegistry,
)
from hermes_vpet_bridge import BridgeServer
from model_library import ModelImportError, ModelLibrary, default_data_dir
from settings_store import SecretProtectionError, SettingsStore
from voice_services import (
    PushToTalkHotkey,
    ResilientTtsService,
    SovitsInstaller,
    SpeechUnavailable,
    TtsService,
)


PROJECT_DIR = Path(__file__).resolve().parent
WEB_DIR = PROJECT_DIR / "web"
PET_HTML = WEB_DIR / "pet.html"
BASE_SIZE = (420, 560)
BUBBLE_SIZE = (360, 220)
BUBBLE_GAP = 12
OPEN_SOURCE_LIVE2D_FILES = (
    WEB_DIR / "vendor" / "pixi.min.js",
    WEB_DIR / "vendor" / "pixi-live2d-display.min.js",
    WEB_DIR / "vendor" / "cubism2.min.js",
)
LIVE2D_GUIDE_URL = "https://www.live2d.com/en/learn/sample/"
VOICE_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a"})
PIPER_MODEL_EXTENSION = ".onnx"
PIPER_CONFIG_EXTENSION = ".onnx.json"
PIPER_RUNTIME = PROJECT_DIR / "assets" / "piper-runtime" / "piper" / "piper.exe"


def voice_data_root(fallback: Path) -> Path:
    if os.environ.get("HATTIP_LAB_DATA_DIR") or os.environ.get("HERMES_PET_DATA_DIR"):
        return fallback
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return fallback
    current = Path(appdata) / "HatTipLab"
    legacy = Path(appdata) / "HermesPet"
    return legacy if legacy.exists() and not current.exists() else current


def list_voice_profiles(data_root: Path) -> list[dict[str, str]]:
    voices_root = (data_root / "voices").resolve()
    if not voices_root.is_dir():
        return []
    profiles: list[dict[str, str]] = []
    for path in sorted(voices_root.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.casefold() not in VOICE_EXTENSIONS:
            continue
        try:
            relative = path.resolve().relative_to(voices_root).as_posix()
        except ValueError:
            continue
        profiles.append({"id": relative, "name": path.stem[:80]})
    return profiles[:100]


def resolve_voice_profile(data_root: Path, profile_id: Any) -> Path | None:
    normalized = str(profile_id or "").replace("\\", "/").strip("/")
    if not normalized or ".." in normalized.split("/"):
        return None
    voices_root = (data_root / "voices").resolve()
    candidate = (voices_root / normalized).resolve()
    try:
        candidate.relative_to(voices_root)
    except ValueError:
        return None
    if candidate.suffix.casefold() not in VOICE_EXTENSIONS:
        return None
    return candidate if candidate.is_file() else None


def resolve_piper_model(data_root: Path, model_name: Any = "") -> Path:
    roots = ((data_root / "piper").resolve(), (PROJECT_DIR / "assets" / "piper").resolve())
    normalized = Path(str(model_name or "")).name
    for root in roots:
        if normalized:
            candidate = root / normalized
            if candidate.is_file() and candidate.with_suffix(PIPER_CONFIG_EXTENSION).is_file():
                return candidate
        if root.is_dir():
            for candidate in sorted(root.glob(f"*{PIPER_MODEL_EXTENSION}")):
                if candidate.with_suffix(PIPER_CONFIG_EXTENSION).is_file():
                    return candidate
    return roots[0] / "zh_CN-approved-medium.onnx"


def configure_tts_service(
    service: TtsService | None, settings: SettingsStore, data_root: Path
) -> None:
    if service is None:
        return
    saved = settings.as_dict()
    profile_id = saved.get("gpt_sovits_voice", "")
    profile = resolve_voice_profile(data_root, profile_id)
    service.configure(
        engine=saved.get("tts_engine", "edge"),
        model_path=str(resolve_piper_model(data_root, saved.get("piper_model", ""))),
        api_url=saved.get("gpt_sovits_url", ""),
        ref_audio_path=str(profile) if profile else "",
        prompt_text=saved.get("gpt_sovits_prompt_text", ""),
        prompt_lang=saved.get("gpt_sovits_prompt_lang", "zh"),
        text_lang=saved.get("gpt_sovits_text_lang", "zh"),
        voice=Path(str(profile_id)).stem if profile_id else "",
    )


def configure_remote_providers(registry: ProviderRegistry, settings: SettingsStore) -> None:
    """Refresh configurable providers without replacing the shared registry."""

    saved = settings.as_dict()
    try:
        openai_key = settings.get_secret("openai_api_key")
    except SecretProtectionError:
        openai_key = ""
    try:
        http_key = settings.get_secret("http_api_key")
    except SecretProtectionError:
        http_key = ""
    registry.register(
        OpenAIResponsesProvider(
            api_key=openai_key or os.environ.get("OPENAI_API_KEY", ""),
            model=str(saved.get("openai_model") or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")),
            base_url=str(
                saved.get("openai_base_url")
                or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            ),
            temperature=float(saved.get("temperature", 0.8)),
            max_tokens=int(saved.get("max_tokens", 1200)),
        )
    )
    registry.register(
        HttpProvider(
            endpoint=str(saved.get("http_endpoint", "")),
            api_key=http_key,
            model=str(saved.get("http_model", "")),
            temperature=float(saved.get("temperature", 0.8)),
            max_tokens=int(saved.get("max_tokens", 1200)),
        )
    )


def enable_per_monitor_dpi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def apply_windows_transparency(
    window: Any, debug: bool = False, show_in_taskbar: bool = True
) -> None:
    """Let DWM composite WebView2 alpha while preserving mouse interaction."""

    try:
        import ctypes

        from System.Drawing import Color
        from webview.platforms.winforms import BrowserView

        class Margins(ctypes.Structure):
            _fields_ = (
                ("left", ctypes.c_int),
                ("right", ctypes.c_int),
                ("top", ctypes.c_int),
                ("bottom", ctypes.c_int),
            )

        form = BrowserView.instances.get(window.uid)
        if form is None:
            raise RuntimeError("native window is not ready")
        form.ShowInTaskbar = show_in_taskbar
        form.TransparencyKey = Color.Empty
        form.BackColor = Color.Black
        form.browser.webview.DefaultBackgroundColor = Color.Transparent
        margins = Margins(-1, -1, -1, -1)
        result = ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
            int(form.Handle.ToInt64()), ctypes.byref(margins)
        )
        if result != 0:
            raise OSError(f"DwmExtendFrameIntoClientArea failed: {result}")
        def dismiss_floating_ui(*_: Any) -> None:
            threading.Thread(
                target=lambda: window.run_js("window.dismissPetUi?.();"),
                daemon=True,
            ).start()

        form.Deactivate += dismiss_floating_ui
        if debug:
            dpi = ctypes.windll.user32.GetDpiForWindow(int(form.Handle.ToInt64()))
            print(
                "[host] desktop_transparency_ready "
                f"dpi={dpi} size={form.Size.Width}x{form.Size.Height} "
                f"client={form.ClientSize.Width}x{form.ClientSize.Height}",
                flush=True,
            )
    except Exception as exc:
        if debug:
            print(f"[host] desktop_transparency_error: {exc}", flush=True)


class NativeTrayIcon:
    """Windows notification-area icon backed by the existing WinForms loop."""

    def __init__(self, api: Any) -> None:
        from System.Drawing import SystemIcons
        from System.Windows.Forms import (
            ContextMenuStrip,
            NotifyIcon,
            ToolStripMenuItem,
            ToolStripSeparator,
        )

        self._api = api
        self._icon = NotifyIcon()
        self._icon.Icon = SystemIcons.Application
        self._icon.Text = "HatTip Lab"
        menu = ContextMenuStrip()
        show_item = ToolStripMenuItem("显示桌宠")
        settings_item = ToolStripMenuItem("设置…")
        exit_item = ToolStripMenuItem("退出 HatTip Lab")
        show_item.Click += lambda *_: self._api.restore()
        settings_item.Click += lambda *_: self._api.open_settings()
        exit_item.Click += lambda *_: self._api.exit_app()
        menu.Items.Add(show_item)
        menu.Items.Add(settings_item)
        menu.Items.Add(ToolStripSeparator())
        menu.Items.Add(exit_item)
        self._icon.ContextMenuStrip = menu
        self._icon.DoubleClick += lambda *_: self._api.restore()
        self._icon.Visible = True
        self._menu = menu

    def dispose(self) -> None:
        try:
            self._icon.Visible = False
            self._icon.Dispose()
            self._menu.Dispose()
        except Exception:
            pass


class NativeBubbleWindow:
    """Small no-activation WinForms bubble that follows the WebView pet."""

    def __init__(self, api: Any, anchor_form: Any) -> None:
        import ctypes

        from System import Action, Array
        from System.Drawing import Color, Font, Point, RectangleF, Size, SolidBrush, StringFormat
        from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
        from System.Drawing import StringTrimming
        from System.Windows.Forms import (
            BorderStyle,
            Button,
            AutoScaleMode,
            FlatStyle,
            Form,
            FormBorderStyle,
            FormStartPosition,
            Panel,
            ScrollBars,
            TextBox,
            Timer,
        )

        self._api = api
        self._anchor_form = anchor_form
        self._Action = Action
        self._Array = Array
        self._Point = Point
        self._Size = Size
        self._GraphicsPath = GraphicsPath
        self._Color = Color
        self._RectangleF = RectangleF
        self._SolidBrush = SolidBrush
        self._StringFormat = StringFormat
        self._StringTrimming = StringTrimming
        self._SmoothingMode = SmoothingMode
        self._ctypes = ctypes
        self._scale = 0.0
        self._side = "right"
        self._message = ""
        self._display_text = ""
        self._typing_index = 0
        self._thinking_step = 0
        self._animation_mode = "idle"
        self._logical_size = BUBBLE_SIZE

        form = Form()
        form.Text = "HatTip Lab Bubble"
        form.AutoScaleMode = getattr(AutoScaleMode, "None")
        form.StartPosition = FormStartPosition.Manual
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.ShowInTaskbar = False
        form.TopMost = True
        form.Opacity = 1.0
        form.BackColor = Color.FromArgb(1, 2, 3)
        form.TransparencyKey = form.BackColor
        form.Owner = anchor_form

        panel = Panel()
        panel.BackColor = Color.FromArgb(250, 250, 252)
        form.Controls.Add(panel)

        arrow = Panel()
        arrow.BackColor = panel.BackColor
        form.Controls.Add(arrow)

        text_box = TextBox()
        text_box.Multiline = True
        text_box.ReadOnly = True
        text_box.TabStop = False
        text_box.BorderStyle = getattr(BorderStyle, "None")
        text_box.ScrollBars = ScrollBars.Vertical
        text_box.BackColor = panel.BackColor
        text_box.ForeColor = Color.FromArgb(51, 56, 74)
        text_box.Font = Font("Microsoft YaHei UI", 10.5)
        panel.Controls.Add(text_box)

        close_button = Button()
        close_button.Text = "×"
        close_button.TabStop = False
        close_button.FlatStyle = FlatStyle.Flat
        close_button.FlatAppearance.BorderSize = 0
        close_button.BackColor = panel.BackColor
        close_button.ForeColor = Color.FromArgb(123, 129, 149)
        close_button.Font = Font("Segoe UI", 13.0)
        close_button.Click += lambda *_: self._api.hide_bubble()
        panel.Controls.Add(close_button)
        panel.Visible = False
        arrow.Visible = False

        timer = Timer()
        timer.Interval = 30
        timer.Tick += self._on_animation_tick

        self._form = form
        self._panel = panel
        self._arrow = arrow
        self._text_box = text_box
        self._close_button = close_button
        self._timer = timer
        self._text_font = text_box.Font
        self._close_font = close_button.Font
        form.Paint += self._on_paint
        form.MouseClick += self._on_mouse_click
        self._layout(self._current_scale())
        _ = form.Handle

        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(int(form.Handle.ToInt64()), -20)
        user32.SetWindowLongW(
            int(form.Handle.ToInt64()), -20, ex_style | 0x08000000 | 0x00000080
        )

    def _current_scale(self) -> float:
        try:
            return max(
                1.0,
                self._ctypes.windll.user32.GetDpiForWindow(
                    int(self._anchor_form.Handle.ToInt64())
                )
                / 96,
            )
        except Exception:
            return 1.0

    def _rounded_region(self, width: int, height: int, radius: int) -> Any:
        path = self._GraphicsPath()
        diameter = radius * 2
        path.AddArc(0, 0, diameter, diameter, 180, 90)
        path.AddArc(width - diameter - 1, 0, diameter, diameter, 270, 90)
        path.AddArc(
            width - diameter - 1, height - diameter - 1, diameter, diameter, 0, 90
        )
        path.AddArc(0, height - diameter - 1, diameter, diameter, 90, 90)
        path.CloseFigure()
        from System.Drawing import Region

        return Region(path)

    def _layout(self, scale: float) -> None:
        s = lambda value: max(1, round(value * scale))
        self._scale = scale
        self._form.ClientSize = self._Size(s(BUBBLE_SIZE[0]), s(BUBBLE_SIZE[1]))
        self._panel.Location = self._Point(s(22), s(18))
        self._panel.Size = self._Size(s(316), s(180))
        self._panel.Region = self._rounded_region(s(316), s(180), s(25))
        self._text_box.Location = self._Point(s(22), s(26))
        self._text_box.Size = self._Size(s(252), s(132))
        self._close_button.Location = self._Point(s(274), s(9))
        self._close_button.Size = self._Size(s(34), s(34))
        self._arrow.Location = self._Point(s(6 if self._side == "right" else 334), s(84))
        self._arrow.Size = self._Size(s(22), s(30))
        path = self._GraphicsPath()
        if self._side == "right":
            points = self._Array[self._Point](
                [self._Point(s(22), 0), self._Point(s(22), s(30)), self._Point(0, s(15))]
            )
        else:
            points = self._Array[self._Point](
                [self._Point(0, 0), self._Point(s(22), s(15)), self._Point(0, s(30))]
            )
        path.AddPolygon(points)
        from System.Drawing import Region

        self._arrow.Region = Region(path)
        self._arrow.BringToFront()

    def _invoke(self, function: Any) -> None:
        if self._form.IsDisposed:
            return
        if self._form.InvokeRequired:
            self._form.Invoke(self._Action(function))
        else:
            function()

    def _on_animation_tick(self, *_: Any) -> None:
        if self._animation_mode == "typed":
            self._typing_index = min(len(self._message), self._typing_index + 2)
            self._display_text = self._message[: self._typing_index]
            if self._typing_index >= len(self._message):
                self._timer.Stop()
                self._animation_mode = "idle"
        elif self._animation_mode == "thinking":
            self._thinking_step = (self._thinking_step + 1) % 4
            self._display_text = self._message + "." * self._thinking_step
        self._form.Invalidate()

    def _on_paint(self, _sender: Any, event: Any) -> None:
        s = lambda value: max(1, round(value * self._scale))
        graphics = event.Graphics
        graphics.SmoothingMode = self._SmoothingMode.AntiAlias
        bubble_path = self._GraphicsPath()
        width, height, radius = s(316), s(180), s(25)
        diameter = radius * 2
        bubble_path.AddArc(s(22), s(18), diameter, diameter, 180, 90)
        bubble_path.AddArc(s(22) + width - diameter, s(18), diameter, diameter, 270, 90)
        bubble_path.AddArc(
            s(22) + width - diameter,
            s(18) + height - diameter,
            diameter,
            diameter,
            0,
            90,
        )
        bubble_path.AddArc(s(22), s(18) + height - diameter, diameter, diameter, 90, 90)
        bubble_path.CloseFigure()
        if self._side == "right":
            points = self._Array[self._Point](
                [self._Point(s(23), s(84)), self._Point(s(23), s(114)), self._Point(s(6), s(99))]
            )
        else:
            points = self._Array[self._Point](
                [self._Point(s(337), s(84)), self._Point(s(354), s(99)), self._Point(s(337), s(114))]
            )
        bubble_path.AddPolygon(points)
        background = self._SolidBrush(self._Color.FromArgb(250, 250, 252))
        foreground = self._SolidBrush(self._Color.FromArgb(51, 56, 74))
        close_brush = self._SolidBrush(self._Color.FromArgb(123, 129, 149))
        text_format = self._StringFormat()
        text_format.Trimming = self._StringTrimming.EllipsisCharacter
        graphics.FillPath(background, bubble_path)
        graphics.DrawString(
            self._display_text,
            self._text_font,
            foreground,
            self._RectangleF(s(44), s(44), s(250), s(132)),
            text_format,
        )
        graphics.DrawString(
            "×",
            self._close_font,
            close_brush,
            self._RectangleF(s(302), s(24), s(28), s(30)),
        )
        text_format.Dispose()
        close_brush.Dispose()
        foreground.Dispose()
        background.Dispose()
        bubble_path.Dispose()

    def _on_mouse_click(self, _sender: Any, event: Any) -> None:
        scale = self._scale or 1.0
        if 294 * scale <= event.X <= 340 * scale and 16 * scale <= event.Y <= 62 * scale:
            self._api.hide_bubble()

    def set_side(self, side: str) -> None:
        normalized = "left" if side == "left" else "right"

        def update() -> None:
            if normalized != self._side:
                self._side = normalized
                self._layout(self._scale or self._current_scale())
                self._form.Invalidate()

        self._invoke(update)

    def render(self, text: str, thinking: bool = False, typed: bool = False) -> None:
        def update() -> None:
            self._timer.Stop()
            self._message = text
            self._typing_index = 0
            self._thinking_step = 0
            if thinking:
                self._animation_mode = "thinking"
                self._display_text = text
                self._timer.Interval = 350
                self._timer.Start()
            elif typed:
                self._animation_mode = "typed"
                self._display_text = ""
                self._timer.Interval = 30
                self._timer.Start()
            else:
                self._animation_mode = "idle"
                self._display_text = text
            self._form.Invalidate()

        self._invoke(update)

    def move(self, x: int, y: int) -> None:
        def update() -> None:
            scale = self._current_scale()
            if abs(scale - self._scale) > 0.01:
                self._layout(scale)
            self._form.Location = self._Point(round(x * scale), round(y * scale))

        self._invoke(update)

    def show(self) -> None:
        def reveal() -> None:
            self._form.Show(self._anchor_form)
            self._form.Refresh()

        self._invoke(reveal)

    def hide(self) -> None:
        self._invoke(lambda: self._form.Hide())

    def destroy(self) -> None:
        self._invoke(lambda: self._form.Close())

    @property
    def on_top(self) -> bool:
        return bool(self._form.TopMost)

    @on_top.setter
    def on_top(self, value: bool) -> None:
        self._invoke(lambda: setattr(self._form, "TopMost", bool(value)))


class WpfBubbleWindow:
    """Per-pixel transparent WPF bubble hosted on its own STA dispatcher."""

    def __init__(self, api: Any) -> None:
        import clr

        framework_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / (
            r"Microsoft.NET\Framework64\v4.0.30319\WPF"
        )
        for assembly in ("WindowsBase.dll", "PresentationCore.dll", "PresentationFramework.dll"):
            clr.AddReference(str(framework_dir / assembly))
        from System import Action, TimeSpan
        from System.Threading import ApartmentState, Thread, ThreadStart

        self._api = api
        self._Action = Action
        self._TimeSpan = TimeSpan
        self._ready = threading.Event()
        self._side = "right"
        self._message = ""
        self._display_text = ""
        self._typing_index = 0
        self._thinking_step = 0
        self._animation_mode = "idle"
        self._logical_size = BUBBLE_SIZE
        self._thread = Thread(ThreadStart(self._run))
        self._thread.SetApartmentState(ApartmentState.STA)
        self._thread.IsBackground = True
        self._thread.Start()
        if not self._ready.wait(10):
            raise RuntimeError("WPF bubble window failed to start")
        if hasattr(self, "_startup_error"):
            raise RuntimeError(str(self._startup_error))

    def _run(self) -> None:
        try:
            from System.Windows import (
                CornerRadius,
                Point,
                ResizeMode,
                TextWrapping,
                Window,
                WindowStyle,
            )
            from System.Windows.Controls import (
                Border,
                Canvas,
                ScrollBarVisibility,
                ScrollViewer,
                TextBlock,
            )
            from System.Windows.Media import Color, FontFamily, PointCollection, SolidColorBrush
            from System.Windows.Shapes import Line, Polygon, Rectangle
            from System.Windows.Threading import Dispatcher, DispatcherPriority, DispatcherTimer

            transparent = SolidColorBrush(Color.FromArgb(0, 0, 0, 0))
            surface = SolidColorBrush(Color.FromArgb(248, 250, 250, 252))
            foreground = SolidColorBrush(Color.FromRgb(51, 56, 74))
            muted = SolidColorBrush(Color.FromRgb(123, 129, 149))

            window = Window()
            window.Title = "HatTip Lab Bubble"
            window.Width = BUBBLE_SIZE[0]
            window.Height = BUBBLE_SIZE[1]
            window.WindowStyle = getattr(WindowStyle, "None")
            window.ResizeMode = ResizeMode.NoResize
            window.AllowsTransparency = True
            window.Background = transparent
            window.ShowInTaskbar = False
            window.ShowActivated = False
            window.Topmost = True

            root = Canvas()
            root.Background = transparent
            window.Content = root

            tail = Polygon()
            tail.Fill = surface
            root.Children.Add(tail)

            border = Border()
            border.Width = 316
            border.Height = 180
            border.CornerRadius = CornerRadius(26)
            border.Background = surface
            Canvas.SetLeft(border, 22)
            Canvas.SetTop(border, 18)
            root.Children.Add(border)

            content = Canvas()
            border.Child = content
            scroll = ScrollViewer()
            scroll.Width = 250
            scroll.Height = 132
            scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
            scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
            Canvas.SetLeft(scroll, 22)
            Canvas.SetTop(scroll, 26)
            content.Children.Add(scroll)

            text = TextBlock()
            text.FontFamily = FontFamily("Microsoft YaHei UI")
            text.FontSize = 15
            text.LineHeight = 25
            text.Foreground = foreground
            text.TextWrapping = TextWrapping.Wrap
            scroll.Content = text

            close_lines = []
            for coordinates in ((285, 15, 295, 25), (295, 15, 285, 25)):
                line = Line()
                line.X1, line.Y1, line.X2, line.Y2 = coordinates
                line.Stroke = muted
                line.StrokeThickness = 1.6
                content.Children.Add(line)
                close_lines.append(line)
            close_hitbox = Rectangle()
            close_hitbox.Width = 34
            close_hitbox.Height = 34
            close_hitbox.Fill = transparent
            close_hitbox.MouseLeftButtonUp += lambda *_: self._api.hide_bubble()
            Canvas.SetLeft(close_hitbox, 274)
            Canvas.SetTop(close_hitbox, 6)
            content.Children.Add(close_hitbox)

            timer = DispatcherTimer()
            timer.Tick += self._on_animation_tick

            self._Point = Point
            self._PointCollection = PointCollection
            self._window = window
            self._tail = tail
            self._border = border
            self._scroll = scroll
            self._text = text
            self._close_lines = close_lines
            self._close_hitbox = close_hitbox
            self._timer = timer
            self._dispatcher = Dispatcher.CurrentDispatcher
            self._DispatcherPriority = DispatcherPriority
            self._update_tail()
            window.Show()
            window.Hide()
            self._ready.set()
            Dispatcher.Run()
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()

    def _invoke(self, function: Any) -> None:
        if not self._ready.wait(10):
            raise RuntimeError("WPF bubble window is not ready")
        if hasattr(self, "_startup_error"):
            raise RuntimeError(str(self._startup_error))
        if self._dispatcher.CheckAccess():
            function()
        else:
            self._dispatcher.Invoke(self._Action(function))

    def _update_tail(self) -> None:
        points = self._PointCollection()
        center = max(56, min(self._logical_size[1] - 48, round(self._logical_size[1] * 0.5)))
        if self._side == "right":
            for point in ((23, center - 15), (23, center + 15), (6, center)):
                points.Add(self._Point(*point))
        else:
            edge = self._logical_size[0] - 23
            for point in ((edge, center - 15), (self._logical_size[0] - 6, center), (edge, center + 15)):
                points.Add(self._Point(*point))
        self._tail.Points = points

    def _resize_for_message(self, message: str, thinking: bool) -> None:
        max_units = max(
            (
                sum(1.0 if ord(character) > 255 else 0.55 for character in paragraph)
                for paragraph in (message.splitlines() or [message])
            ),
            default=1,
        )
        width = 180 if thinking else max(250, min(BUBBLE_SIZE[0], round(150 + max_units * 8)))
        text_width = max(70, width - 110)
        if thinking:
            height = 118
        else:
            logical_lines = 0
            for paragraph in (message.splitlines() or [message]):
                width_units = sum(1.0 if ord(character) > 255 else 0.55 for character in paragraph)
                units_per_line = max(8, text_width / 14)
                logical_lines += max(1, int((width_units + units_per_line - 1) // units_per_line))
            height = max(118, min(BUBBLE_SIZE[1], 78 + logical_lines * 25))
        self._logical_size = (width, height)
        content_width = width - 44
        self._window.Width = width
        self._window.Height = height
        self._border.Width = content_width
        self._border.Height = height - 40
        self._scroll.Width = text_width
        self._scroll.Height = max(42, height - 88)
        close_x = content_width - 31
        for index, line in enumerate(self._close_lines):
            line.X1 = close_x if index == 0 else close_x + 10
            line.X2 = close_x + 10 if index == 0 else close_x
        from System.Windows.Controls import Canvas
        Canvas.SetLeft(self._close_hitbox, content_width - 42)
        self._update_tail()

    def _on_animation_tick(self, *_: Any) -> None:
        if self._animation_mode == "typed":
            self._typing_index = min(len(self._message), self._typing_index + 2)
            self._display_text = self._message[: self._typing_index]
            if self._typing_index >= len(self._message):
                self._timer.Stop()
                self._animation_mode = "idle"
        elif self._animation_mode == "thinking":
            self._thinking_step = (self._thinking_step + 1) % 3
            self._display_text = self._message + "." * (self._thinking_step + 1)
        self._text.Text = self._display_text

    def set_side(self, side: str) -> None:
        def update() -> None:
            self._side = "left" if side == "left" else "right"
            self._update_tail()

        self._invoke(update)

    def render(self, text: str, thinking: bool = False, typed: bool = False) -> None:
        def update() -> None:
            self._timer.Stop()
            self._resize_for_message(text, thinking)
            self._message = "" if thinking else text
            self._typing_index = 0
            self._thinking_step = 0
            if thinking:
                self._animation_mode = "thinking"
                self._display_text = "..."
                self._timer.Interval = self._TimeSpan.FromMilliseconds(240)
                self._timer.Start()
            elif typed:
                self._animation_mode = "typed"
                self._display_text = ""
                self._timer.Interval = self._TimeSpan.FromMilliseconds(30)
                self._timer.Start()
            else:
                self._animation_mode = "idle"
                self._display_text = text
            self._text.Text = self._display_text

        self._invoke(update)

    @property
    def logical_size(self) -> tuple[int, int]:
        return self._logical_size

    def move(self, x: int, y: int) -> None:
        self._invoke(lambda: (setattr(self._window, "Left", x), setattr(self._window, "Top", y)))

    def show(self) -> None:
        self._invoke(lambda: self._window.Show())

    def hide(self) -> None:
        self._invoke(lambda: self._window.Hide())

    def destroy(self) -> None:
        def close() -> None:
            self._timer.Stop()
            self._window.Close()
            self._dispatcher.BeginInvokeShutdown(self._DispatcherPriority.Normal)

        self._invoke(close)

    @property
    def on_top(self) -> bool:
        return bool(self._window.Topmost)

    @on_top.setter
    def on_top(self, value: bool) -> None:
        self._invoke(lambda: setattr(self._window, "Topmost", bool(value)))


class DesktopApi:
    def __init__(
        self,
        bridge_port: int,
        mode: str,
        provider: str,
        settings: SettingsStore,
        providers: ProviderRegistry,
        models: ModelLibrary,
        tts_service: TtsService | None = None,
        mock: bool = False,
    ) -> None:
        self._bridge_port = bridge_port
        self._bridge_url = f"http://127.0.0.1:{bridge_port}"
        self._requested_mode = mode
        self._requested_provider = provider
        self._settings = settings
        self._providers = providers
        self._models = models
        self._voice_root = voice_data_root(self._models.root)
        self._tts_service = tts_service
        self._mock = mock
        self._window: Any | None = None
        self._settings_window: Any | None = None
        self._settings_closing_handler: Any | None = None
        self._settings_window_lock = threading.RLock()
        self._tray: NativeTrayIcon | None = None
        self._hotkey: PushToTalkHotkey | None = None
        self._bubble_window: Any | None = None
        self._bubble_ready = threading.Event()
        self._bubble_visible = False
        self._bubble_hide_timer: threading.Timer | None = None
        self._bubble_lock = threading.RLock()
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._drag_lock = threading.Lock()
        self._sovits_installer = SovitsInstaller(self._voice_root / "sovits")
        self._sovits_install_thread: threading.Thread | None = None
        self._sovits_install_lock = threading.Lock()
        self._exiting = False

    def _attach_window(self, window: Any) -> None:
        self._window = window

    def _attach_hotkey(self, hotkey: PushToTalkHotkey) -> None:
        self._hotkey = hotkey

    def _attach_tray(self, tray: NativeTrayIcon) -> None:
        self._tray = tray

    def _attach_settings_window(self, window: Any) -> None:
        def hide_instead_of_destroying(*_: Any) -> bool:
            try:
                window.hide()
            except Exception:
                pass
            return False

        def clear(*_: Any) -> None:
            with self._settings_window_lock:
                if self._settings_window is window:
                    self._settings_window = None
                    self._settings_closing_handler = None

        with self._settings_window_lock:
            self._settings_window = window
            self._settings_closing_handler = hide_instead_of_destroying
        window.events.closing += hide_instead_of_destroying
        window.events.closed += clear

    def _create_settings_window(self, *, hidden: bool = False) -> Any:
        import webview

        window = webview.create_window(
            "HatTip Lab 设置",
            url=f"{self._bridge_url}/ui/settings.html",
            js_api=self,
            width=820,
            height=760,
            min_size=(560, 520),
            hidden=hidden,
            resizable=True,
            on_top=True,
            background_color="#f4f2fb",
            text_select=True,
        )
        if window is None:
            raise RuntimeError("设置窗口初始化失败")
        self._attach_settings_window(window)
        return window

    def _destroy_settings_window(self) -> None:
        with self._settings_window_lock:
            window = self._settings_window
            closing_handler = self._settings_closing_handler
            self._settings_window = None
            self._settings_closing_handler = None
        if window is not None:
            try:
                if closing_handler is not None:
                    window.events.closing -= closing_handler
                window.destroy()
            except Exception:
                pass

    def _attach_bubble_window(self, window: Any) -> None:
        self._bubble_window = window
        self._bubble_ready.set()

    def _destroy_bubble_window(self) -> None:
        self._cancel_bubble_timer()
        with self._bubble_lock:
            window = self._bubble_window
            self._bubble_window = None
            self._bubble_visible = False
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    @staticmethod
    def _calculate_bubble_position(
        pet: tuple[int, int, int, int],
        bubble: tuple[int, int],
        work_area: tuple[int, int, int, int],
        gap: int = BUBBLE_GAP,
    ) -> tuple[int, int, str]:
        """Return a clamped bubble position and the side it occupies."""

        pet_x, pet_y, pet_width, pet_height = pet
        bubble_width, bubble_height = bubble
        work_left, work_top, work_right, work_bottom = work_area
        right_x = pet_x + pet_width + gap
        left_x = pet_x - bubble_width - gap
        room_right = work_right - (pet_x + pet_width)
        room_left = pet_x - work_left
        prefer_right = pet_x + pet_width / 2 <= (work_left + work_right) / 2

        if prefer_right and right_x + bubble_width <= work_right:
            side = "right"
            bubble_x = right_x
        elif not prefer_right and left_x >= work_left:
            side = "left"
            bubble_x = left_x
        elif room_right >= room_left:
            side = "right"
            bubble_x = min(right_x, work_right - bubble_width)
        else:
            side = "left"
            bubble_x = max(work_left, left_x)

        head_offset = min(112, round(pet_height * 0.18))
        bubble_y = pet_y + head_offset
        bubble_x = max(work_left, min(round(bubble_x), work_right - bubble_width))
        bubble_y = max(work_top, min(round(bubble_y), work_bottom - bubble_height))
        return bubble_x, bubble_y, side

    def _window_geometry(self) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        if self._window is None:
            raise RuntimeError("pet window is not ready")
        pet = (
            int(self._window.x),
            int(self._window.y),
            int(self._window.width),
            int(self._window.height),
        )
        try:
            from System.Windows.Forms import Screen
            from webview.platforms.winforms import BrowserView

            form = BrowserView.instances.get(self._window.uid)
            if form is None:
                raise RuntimeError("native window is not ready")
            scale = float(form._scale) or 1.0
            area = Screen.FromHandle(form.Handle).WorkingArea
            work_area = (
                round(area.Left / scale),
                round(area.Top / scale),
                round(area.Right / scale),
                round(area.Bottom / scale),
            )
        except Exception:
            # Unit tests and non-Windows preview hosts use a conventional fallback.
            work_area = (0, 0, 1920, 1080)
        return pet, work_area

    def _position_bubble(self) -> str | None:
        if self._window is None or self._bubble_window is None:
            return None
        try:
            pet, work_area = self._window_geometry()
            bubble_size = getattr(self._bubble_window, "logical_size", BUBBLE_SIZE)
            x, y, side = self._calculate_bubble_position(pet, bubble_size, work_area)
            self._bubble_window.move(x, y)
            if hasattr(self._bubble_window, "set_side"):
                self._bubble_window.set_side(side)
            else:
                self._bubble_window.evaluate_js(
                    f"window.setPetBubbleSide?.({json.dumps(side)});"
                )
            return side
        except Exception:
            return None

    def _cancel_bubble_timer(self) -> None:
        with self._bubble_lock:
            timer = self._bubble_hide_timer
            self._bubble_hide_timer = None
        if timer is not None:
            timer.cancel()

    def show_bubble(self, text: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._bubble_window is None:
            return {"ok": False, "error": "气泡窗口尚未就绪"}
        message = str(text).strip()[:4000]
        if not message:
            return {"ok": False, "error": "气泡内容为空"}
        settings = options if isinstance(options, dict) else {}
        thinking = bool(settings.get("thinking"))
        typed = bool(settings.get("typed"))
        stay = bool(settings.get("stay"))
        self._cancel_bubble_timer()
        try:
            if hasattr(self._bubble_window, "render"):
                self._bubble_window.render(message, thinking=thinking, typed=typed)
            side = self._position_bubble() or "right"
            payload = json.dumps(
                {"text": message, "thinking": thinking, "typed": typed, "side": side},
                ensure_ascii=False,
            )
            if not hasattr(self._bubble_window, "render"):
                self._bubble_window.evaluate_js(f"window.setPetBubble({payload});")
            self._bubble_window.show()
            with self._bubble_lock:
                self._bubble_visible = True
            return {"ok": True, "side": side}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def hide_bubble(self) -> dict[str, bool]:
        self._cancel_bubble_timer()
        with self._bubble_lock:
            was_visible = self._bubble_visible
            self._bubble_visible = False
        if self._bubble_window is not None and was_visible:
            try:
                self._bubble_window.hide()
            except Exception:
                return {"ok": False}
        return {"ok": True}

    def _model_payloads(self) -> list[dict[str, object]]:
        return [model.to_dict(self._bridge_url) for model in self._models.list_models()]

    def _selected_provider(self, saved: dict[str, Any]) -> str:
        requested = self._requested_provider if self._requested_provider != "auto" else saved.get("provider")
        if requested and self._providers.has_available(str(requested)):
            return str(requested)
        available = [item for item in self._providers.list() if item["available"]]
        return str(available[0]["id"]) if available else self._providers.default_id

    def _repair_selected_provider(self, saved: dict[str, Any]) -> str:
        provider = self._selected_provider(saved)
        if saved.get("provider") != provider:
            self._settings.update(provider=provider)
        return provider

    def bootstrap(self) -> dict[str, Any]:
        saved = self._settings.as_dict()
        model_payloads = self._model_payloads()
        selected_id = saved.get("model_id")
        selected_model = next((model for model in model_payloads if model["id"] == selected_id), None)
        if selected_model is None and model_payloads:
            selected_model = model_payloads[0]
            selected_id = selected_model["id"]
        runtime_available = self._models.runtime_available()
        available = bool(runtime_available and selected_model)
        mode = self._requested_mode if self._requested_mode != "auto" else saved.get("mode", "gif")
        if mode == "live2d" and not available:
            mode = "gif"
        provider = self._repair_selected_provider(saved)
        return {
            "mode": mode,
            "provider": provider,
            "providers": self._providers.list(),
            "models": model_payloads,
            "model_id": selected_id,
            "selected_model": selected_model,
            "scale": saved.get("scale", 1.0),
            "on_top": saved.get("on_top", True),
            "live2d_available": available,
            "live2d_runtime_available": runtime_available,
            "live2d_runtime_url": f"{self._bridge_url}/runtime/live2dcubismcore.min.js",
            "external_bubble": self._bubble_window is not None,
            "voice_input_enabled": bool(saved.get("voice_input_enabled", True)),
            "push_to_talk_hotkey": saved.get("push_to_talk_hotkey", "Alt+Space"),
            "tts_enabled": bool(saved.get("tts_enabled", False)),
            "tts_voice": saved.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
            "tts_available": bool(self._tts_service and self._tts_service.available),
            "tts_engine": saved.get("tts_engine", "edge"),
            "idle_animations": bool(saved.get("idle_animations", True)),
            "mock": self._mock,
        }

    def get_settings(self) -> dict[str, Any]:
        saved = self._settings.as_dict()
        tts_status = (
            self._tts_service.status(probe=False)
            if self._tts_service and hasattr(self._tts_service, "status")
            else {"available": False, "piper_available": False, "gpt_sovits_online": False}
        )
        return {
            "ok": True,
            **saved,
            "providers": self._providers.list(),
            "models": self._model_payloads(),
            "openai_api_key_configured": self._settings.has_secret("openai_api_key")
            or bool(os.environ.get("OPENAI_API_KEY")),
            "http_api_key_configured": self._settings.has_secret("http_api_key"),
            "live2d_runtime_available": self._models.runtime_available(),
            "tts_available": bool(self._tts_service and self._tts_service.available),
            "tts_status": tts_status,
            "voice_profiles": list_voice_profiles(self._voice_root),
            "sovits_install": self._sovits_installer.status(),
        }

    @staticmethod
    def _validated_url(value: Any, label: str, allow_empty: bool = False) -> str:
        normalized = str(value or "").strip().rstrip("/")
        if not normalized and allow_empty:
            return ""
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{label}必须是 http:// 或 https:// 地址")
        return normalized

    def _restart_hotkey(self) -> None:
        if self._hotkey is None:
            return
        saved = self._settings.as_dict()
        if not bool(saved.get("voice_input_enabled", True)):
            self._hotkey.stop()
            return
        self._hotkey.start(str(saved.get("push_to_talk_hotkey", "Alt+Space")))

    def _notify_main_settings_changed(self) -> None:
        if self._window is None:
            return
        try:
            self._window.run_js("window.onPetSettingsChanged?.();")
        except Exception:
            pass

    def save_settings(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        values = options if isinstance(options, dict) else {}
        try:
            temperature = max(0.0, min(float(values.get("temperature", 0.8)), 2.0))
            max_tokens = max(64, min(int(values.get("max_tokens", 1200)), 128000))
            hotkey = PushToTalkHotkey.validate(
                str(values.get("push_to_talk_hotkey", "Alt+Space"))
            )
            openai_base_url = self._validated_url(
                values.get("openai_base_url", "https://api.openai.com/v1"),
                "OpenAI 服务地址",
            )
            http_endpoint = self._validated_url(
                values.get("http_endpoint", ""), "HTTP Provider 地址", allow_empty=True
            )
            gpt_sovits_url = self._validated_url(
                values.get("gpt_sovits_url", ""), "GPT-SoVITS 地址", allow_empty=True
            )
            mode = str(values.get("mode", "gif"))
            if mode not in {"gif", "live2d"}:
                raise ValueError("未知角色模式")
            model_id = values.get("model_id")
            if model_id and self._models.get(str(model_id)) is None:
                raise ValueError("找不到所选 Live2D 模型")
            if mode == "live2d" and not (
                self._models.runtime_available() and (model_id or self._models.list_models())
            ):
                raise ValueError("请先安装 Cubism Core 并导入 Live2D 模型")
            tts_enabled = bool(values.get("tts_enabled", False))
            tts_engine = str(values.get("tts_engine", "edge")).casefold()
            if tts_engine == "auto":
                tts_engine = "gpt-sovits"
            if tts_engine not in {"edge", "piper", "gpt-sovits"}:
                raise ValueError("未知 TTS 引擎")
            voice_profile = str(values.get("gpt_sovits_voice", ""))
            if voice_profile and resolve_voice_profile(self._voice_root, voice_profile) is None:
                raise ValueError("找不到所选参考音频")
            scale = min((0.8, 1.0, 1.2), key=lambda item: abs(item - float(values.get("scale", 1.0))))
            requested_provider = str(values.get("provider", "hermes"))
            self._providers.get(requested_provider, require_available=False)
            openai_key_ready = bool(
                str(values.get("openai_api_key", "")).strip()
                or (
                    not values.get("clear_openai_api_key")
                    and (
                        self._settings.has_secret("openai_api_key")
                        or os.environ.get("OPENAI_API_KEY")
                    )
                )
            )
            if requested_provider == "openai" and not openai_key_ready:
                raise ValueError("请先填写 OpenAI API Key")
            if requested_provider == "http" and not (
                http_endpoint and str(values.get("http_model", "")).strip()
            ):
                raise ValueError("请填写 HTTP Provider 地址和模型 ID")
            if requested_provider == "hermes" and not self._providers.has_available("hermes"):
                raise ValueError("未检测到 Hermes CLI，请先安装并确认 hermes 命令可用")
            changes = {
                "provider": requested_provider,
                "openai_model": str(values.get("openai_model", "gpt-4o-mini")).strip()
                or "gpt-4o-mini",
                "openai_base_url": openai_base_url,
                "http_endpoint": http_endpoint,
                "http_model": str(values.get("http_model", "")).strip(),
                "temperature": temperature,
                "max_tokens": max_tokens,
                "mode": mode,
                "model_id": str(model_id) if model_id else None,
                "voice_input_enabled": bool(values.get("voice_input_enabled", True)),
                "push_to_talk_hotkey": hotkey,
                "tts_enabled": tts_enabled,
                "tts_engine": tts_engine,
                "tts_voice": str(values.get("tts_voice", "zh-CN-XiaoxiaoNeural")).strip()
                or "zh-CN-XiaoxiaoNeural",
                "piper_model": Path(str(values.get("piper_model", ""))).name,
                "gpt_sovits_url": gpt_sovits_url,
                "gpt_sovits_voice": voice_profile,
                "gpt_sovits_prompt_text": str(values.get("gpt_sovits_prompt_text", "")).strip()[:500],
                "gpt_sovits_prompt_lang": str(values.get("gpt_sovits_prompt_lang", "zh")).strip() or "zh",
                "gpt_sovits_text_lang": str(values.get("gpt_sovits_text_lang", "zh")).strip() or "zh",
                "idle_animations": bool(values.get("idle_animations", True)),
                "minimize_to_tray": bool(values.get("minimize_to_tray", True)),
                "on_top": bool(values.get("on_top", True)),
                "scale": scale,
            }
            secret_changes: dict[str, str] = {}
            for secret_name in ("openai_api_key", "http_api_key"):
                if values.get(f"clear_{secret_name}"):
                    secret_changes[secret_name] = ""
                elif str(values.get(secret_name, "")).strip():
                    secret_changes[secret_name] = str(values[secret_name])
            self._settings.apply(changes=changes, secret_changes=secret_changes)
            configure_remote_providers(self._providers, self._settings)
            configure_tts_service(self._tts_service, self._settings, self._voice_root)
            if not self._providers.has_available(requested_provider):
                raise RuntimeError("保存后 Provider 状态异常")
            self._restart_hotkey()
            self.set_scale(scale)
            self.set_on_top(bool(values.get("on_top", True)))
            self._notify_main_settings_changed()
            return self.get_settings()
        except (TypeError, ValueError, KeyError, RuntimeError, SecretProtectionError) as exc:
            return {"ok": False, "error": str(exc)}

    def open_settings(self) -> dict[str, bool]:
        with self._settings_window_lock:
            existing = self._settings_window
        if existing is not None:
            try:
                existing.show()
                existing.restore()
                return {"ok": True}
            except Exception:
                with self._settings_window_lock:
                    self._settings_window = None

        def create() -> None:
            try:
                self._create_settings_window()
            except Exception:
                with self._settings_window_lock:
                    self._settings_window = None
                    self._settings_closing_handler = None

        threading.Thread(target=create, name="hattip-lab-settings", daemon=True).start()
        return {"ok": True}

    def close_settings(self) -> dict[str, bool]:
        with self._settings_window_lock:
            window = self._settings_window
        if window is not None:
            try:
                window.hide()
            except Exception:
                return {"ok": False}
        return {"ok": True}

    def on_push_to_talk(self, active: bool) -> None:
        if self._window is None:
            return
        try:
            value = "true" if active else "false"
            self._window.run_js(f"window.onPushToTalk?.({value});")
        except Exception:
            pass

    def synthesize_speech(self, text: str, emotion: str = "neutral") -> dict[str, Any]:
        saved = self._settings.as_dict()
        if not bool(saved.get("tts_enabled", False)):
            return {"ok": False, "disabled": True}
        if self._tts_service is None or not self._tts_service.available:
            return {"ok": False, "error": "TTS 组件不可用，请重新安装完整版本"}
        try:
            artifact = self._tts_service.synthesize_result(
                str(text),
                str(saved.get("tts_voice", "")),
                str(emotion).casefold(),
            )
            return {
                "ok": True,
                "url": f"{self._bridge_url}/audio/{artifact.token}.{artifact.extension}",
                "engine": artifact.engine,
                "fallback": artifact.fallback,
            }
        except SpeechUnavailable as exc:
            return {"ok": False, "fallback": "text", "error": f"语音服务暂不可用：{exc}"}
        except Exception as exc:
            return {"ok": False, "error": f"语音生成失败：{exc}"}

    def chat(self, text: str) -> dict[str, Any]:
        selected = self._repair_selected_provider(self._settings.as_dict())
        try:
            response = requests.post(
                f"{self._bridge_url}/chat",
                json={"text": text, "provider": selected, "stream": False},
                timeout=130,
            )
            payload = response.json()
        except requests.RequestException as exc:
            return {"ok": False, "error": f"无法连接智能体：{exc}"}
        except ValueError:
            return {"ok": False, "error": "智能体返回了无法识别的数据"}
        if not response.ok:
            return {"ok": False, "error": payload.get("error", "智能体请求失败")}
        return payload

    def set_provider(self, provider_id: str) -> dict[str, Any]:
        if not self._providers.has_available(provider_id):
            return {"ok": False, "error": "所选智能体当前不可用"}
        self._settings.update(provider=provider_id)
        return {"ok": True, "provider": provider_id}

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in {"gif", "live2d"}:
            return {"ok": False, "error": "未知角色模式"}
        if mode == "live2d" and not (self._models.runtime_available() and self._models.list_models()):
            return {"ok": False, "error": "请先安装 Cubism Core 并导入自己的 Live2D 模型"}
        self._settings.update(mode=mode)
        return {"ok": True, "mode": mode}

    def set_model(self, model_id: str, notify: bool = True) -> dict[str, Any]:
        model = self._models.get(model_id)
        if model is None:
            return {"ok": False, "error": "找不到所选模型"}
        self._settings.update(model_id=model.id)
        if notify:
            self._notify_main_settings_changed()
        return {
            "ok": True,
            "model_id": model.id,
            "model": model.to_dict(self._bridge_url),
            "live2d_available": self._models.runtime_available(),
        }

    def _open_file_dialog(self, file_types: tuple[str, ...]) -> Path | None:
        if self._window is None:
            return None
        import webview

        dialog_type = getattr(getattr(webview, "FileDialog", object), "OPEN", None)
        if dialog_type is None:
            dialog_type = webview.OPEN_DIALOG
        selected = self._window.create_file_dialog(dialog_type, allow_multiple=False, file_types=file_types)
        if not selected:
            return None
        return Path(selected[0])

    def import_live2d_model(self) -> dict[str, Any]:
        source = self._open_file_dialog(
            (
                "Live2D 模型 (*.model3.json;*.model.json;*.zip)",
                "所有文件 (*.*)",
            )
        )
        if source is None:
            return {"ok": False, "cancelled": True}
        try:
            model = self._models.import_model(source)
        except (ModelImportError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self._settings.update(model_id=model.id)
        self._notify_main_settings_changed()
        return {
            "ok": True,
            "model": model.to_dict(self._bridge_url),
            "models": self._model_payloads(),
            "live2d_available": self._models.runtime_available(),
        }

    def install_live2d_core(self) -> dict[str, Any]:
        source = self._open_file_dialog(("Live2D Cubism Core (live2dcubismcore.min.js)", "JavaScript (*.js)"))
        if source is None:
            return {"ok": False, "cancelled": True}
        try:
            self._models.install_core(source)
        except (ModelImportError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        self._notify_main_settings_changed()
        return {
            "ok": True,
            "live2d_runtime_available": True,
            "live2d_runtime_url": f"{self._bridge_url}/runtime/live2dcubismcore.min.js",
            "live2d_available": bool(self._models.list_models()),
        }

    def import_piper_model(self) -> dict[str, Any]:
        source = self._open_file_dialog(("Piper 语音模型 (*.onnx)", "所有文件 (*.*)"))
        if source is None:
            return {"ok": False, "cancelled": True}
        config = source.with_suffix(PIPER_CONFIG_EXTENSION)
        if source.suffix.casefold() != PIPER_MODEL_EXTENSION or not config.is_file():
            return {"ok": False, "error": "模型旁必须有同名 .onnx.json 配置文件"}
        try:
            if source.stat().st_size > 512 * 1024 * 1024:
                raise ValueError("Piper 模型超过 512 MB，已拒绝导入")
            destination_root = self._voice_root / "piper"
            destination_root.mkdir(parents=True, exist_ok=True)
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip(".-") or "voice"
            name = f"{safe_stem}-{uuid.uuid4().hex[:6]}.onnx"
            destination = destination_root / name
            shutil.copy2(source, destination)
            shutil.copy2(config, destination.with_suffix(PIPER_CONFIG_EXTENSION))
            self._settings.update(piper_model=name)
            configure_tts_service(self._tts_service, self._settings, self._voice_root)
            self._notify_main_settings_changed()
            return {"ok": True, "piper_model": name}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def import_voice_profile(self) -> dict[str, Any]:
        source = self._open_file_dialog(
            ("参考音频 (*.wav;*.mp3;*.flac;*.ogg;*.m4a)", "所有文件 (*.*)")
        )
        if source is None:
            return {"ok": False, "cancelled": True}
        if source.suffix.casefold() not in VOICE_EXTENSIONS:
            return {"ok": False, "error": "仅支持 WAV、MP3、FLAC、OGG 或 M4A"}
        try:
            if source.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("参考音频超过 128 MB")
            voices_root = self._voice_root / "voices"
            voices_root.mkdir(parents=True, exist_ok=True)
            safe_stem = re.sub(r"[^A-Za-z0-9._\u4e00-\u9fff-]+", "-", source.stem).strip(".-") or "voice"
            destination = voices_root / f"{safe_stem}-{uuid.uuid4().hex[:6]}{source.suffix.casefold()}"
            shutil.copy2(source, destination)
            profile_id = destination.relative_to(voices_root).as_posix()
            self._settings.update(gpt_sovits_voice=profile_id)
            configure_tts_service(self._tts_service, self._settings, self._voice_root)
            return {
                "ok": True,
                "voice": {"id": profile_id, "name": destination.stem[:80]},
                "voices": list_voice_profiles(self._voice_root),
            }
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _notify_sovits_progress(self, payload: dict[str, Any]) -> None:
        script = f"window.onSovitsInstallProgress?.({json.dumps(payload, ensure_ascii=False)});"
        for window in (self._settings_window, self._window):
            if window is None:
                continue
            try:
                window.run_js(script)
            except Exception:
                pass

    def install_sovits(self) -> dict[str, Any]:
        with self._sovits_install_lock:
            if self._sovits_install_thread and self._sovits_install_thread.is_alive():
                return {"ok": True, "started": False, **self._sovits_installer.status()}

            def work() -> None:
                try:
                    self._sovits_installer.install(self._notify_sovits_progress)
                    self._sovits_installer.start()
                    self._settings.update(gpt_sovits_url="http://127.0.0.1:9880")
                    configure_tts_service(self._tts_service, self._settings, self._voice_root)
                    self._notify_sovits_progress(self._sovits_installer.status())
                except Exception:
                    self._notify_sovits_progress(self._sovits_installer.status())

            self._sovits_install_thread = threading.Thread(
                target=work, name="hattip-lab-sovits-install", daemon=True
            )
            self._sovits_install_thread.start()
        return {"ok": True, "started": True, **self._sovits_installer.status()}

    def get_sovits_install_status(self) -> dict[str, Any]:
        return {"ok": True, **self._sovits_installer.status()}

    def start_sovits(self) -> dict[str, Any]:
        try:
            status = self._sovits_installer.start()
            self._settings.update(gpt_sovits_url="http://127.0.0.1:9880")
            configure_tts_service(self._tts_service, self._settings, self._voice_root)
            return {"ok": True, **status}
        except (OSError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}

    def open_model_guide(self) -> dict[str, bool]:
        webbrowser.open(LIVE2D_GUIDE_URL)
        return {"ok": True}

    def report_client_status(self, event: str, details: str = "") -> dict[str, bool]:
        if event in {"ui_ready", "live2d_ready", "live2d_error"}:
            safe_details = " ".join(str(details).split())[:300]
            print(f"[client] {event}: {safe_details}", flush=True)
        return {"ok": True}

    def begin_drag(self, screen_x: float, screen_y: float) -> dict[str, bool]:
        if self._window is None:
            return {"ok": False}
        try:
            origin = (
                round(float(screen_x)),
                round(float(screen_y)),
                int(self._window.x),
                int(self._window.y),
            )
            with self._drag_lock:
                self._drag_origin = origin
            return {"ok": True}
        except (TypeError, ValueError, AttributeError):
            return {"ok": False}

    def drag_to(self, screen_x: float, screen_y: float) -> dict[str, bool]:
        if self._window is None:
            return {"ok": False}
        with self._drag_lock:
            origin = self._drag_origin
        if origin is None:
            return {"ok": False}
        try:
            pointer_x = round(float(screen_x))
            pointer_y = round(float(screen_y))
            self._window.move(
                origin[2] + pointer_x - origin[0],
                origin[3] + pointer_y - origin[1],
            )
            with self._bubble_lock:
                bubble_visible = self._bubble_visible
            if bubble_visible:
                self._position_bubble()
            return {"ok": True}
        except (TypeError, ValueError, AttributeError):
            return {"ok": False}

    def set_scale(self, scale: float) -> dict[str, Any]:
        allowed = (0.8, 1.0, 1.2)
        nearest = min(allowed, key=lambda item: abs(item - float(scale)))
        if self._window is not None:
            new_size = (round(BASE_SIZE[0] * nearest), round(BASE_SIZE[1] * nearest))
            try:
                pet, work_area = self._window_geometry()
                new_x, new_y = self._calculate_scaled_window_position(
                    pet, new_size, work_area
                )
                self._window.resize(*new_size)
                self._window.move(new_x, new_y)
                self._window.run_js("window.onPetHostResize?.();")
            except Exception:
                self._window.resize(*new_size)
        with self._bubble_lock:
            bubble_visible = self._bubble_visible
        if bubble_visible:
            self._position_bubble()
        self._settings.update(scale=nearest)
        return {"ok": True, "scale": nearest}

    @staticmethod
    def _calculate_scaled_window_position(
        pet: tuple[int, int, int, int],
        new_size: tuple[int, int],
        work_area: tuple[int, int, int, int],
    ) -> tuple[int, int]:
        """Resize around the pet's bottom-center and keep the window on-screen."""

        pet_x, pet_y, pet_width, pet_height = pet
        new_width, new_height = new_size
        work_left, work_top, work_right, work_bottom = work_area
        x = round(pet_x + (pet_width - new_width) / 2)
        y = pet_y + pet_height - new_height
        x = max(work_left, min(x, work_right - new_width))
        y = max(work_top, min(y, work_bottom - new_height))
        return x, y

    def set_on_top(self, enabled: bool) -> dict[str, Any]:
        value = bool(enabled)
        if self._window is not None:
            self._window.on_top = value
        if self._bubble_window is not None:
            self._bubble_window.on_top = value
        self._settings.update(on_top=value)
        return {"ok": True, "on_top": value}

    def minimize(self) -> dict[str, bool]:
        self.hide_bubble()
        if self._window is not None:
            if bool(self._settings.as_dict().get("minimize_to_tray", True)) and self._tray is not None:
                self._window.hide()
            else:
                self._window.minimize()
        return {"ok": True}

    def restore(self) -> dict[str, bool]:
        if self._window is not None:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                return {"ok": False}
        return {"ok": True}

    def exit_app(self) -> dict[str, bool]:
        self._exiting = True
        if self._hotkey is not None:
            self._hotkey.stop()
        self._sovits_installer.stop()
        self._destroy_settings_window()
        self._destroy_bubble_window()
        if self._tray is not None:
            self._tray.dispose()
            self._tray = None
        if self._window is not None:
            self._window.destroy()
        return {"ok": True}


def run_check(data_dir: Path | None = None) -> int:
    library = ModelLibrary(data_dir or default_data_dir())
    checks = {
        "Python >= 3.10": sys.version_info >= (3, 10),
        "Hermes CLI": shutil.which("hermes") is not None,
        "Flask": importlib.util.find_spec("flask") is not None,
        "Requests": importlib.util.find_spec("requests") is not None,
        "pywebview": importlib.util.find_spec("webview") is not None,
        "Piper TTS（本地）": PIPER_RUNTIME.is_file() or importlib.util.find_spec("piper") is not None,
        "Edge TTS（在线）": importlib.util.find_spec("edge_tts") is not None,
        "GIF 素材": (WEB_DIR / "assets" / "pet-placeholder.gif").is_file(),
        "Live2D 开源前端": all(path.is_file() for path in OPEN_SOURCE_LIVE2D_FILES),
        "Live2D Core（用户安装）": library.runtime_available(),
        "已导入 Live2D 模型": len(library.list_models()),
        "用户数据目录": str(library.root),
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    required = (
        "Python >= 3.10",
        "Flask",
        "Requests",
        "pywebview",
        "GIF 素材",
        "Live2D 开源前端",
        "Edge TTS（在线）",
    )
    return 0 if all(checks[name] for name in required) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HatTip Lab Windows 桌面宠物")
    parser.add_argument("--mode", choices=("auto", "gif", "live2d"), default="auto")
    parser.add_argument(
        "--provider", choices=("auto", "hermes", "openai", "http", "mock"), default="auto"
    )
    parser.add_argument("--port", type=int, default=0, help="本地智能体桥接端口（默认自动选择）")
    parser.add_argument("--data-dir", type=Path, help="覆盖用户模型和运行时目录")
    parser.add_argument("--mock-hermes", action="store_true", help="使用快速假回复验证 UI")
    parser.add_argument("--debug", action="store_true", help="启用 WebView 开发调试")
    parser.add_argument("--check", action="store_true", help="检查运行环境后退出")
    parser.add_argument("--exit-after", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--bubble-test", help=argparse.SUPPRESS)
    parser.add_argument("--bubble-test-stay", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scale-test", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--layout-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--composer-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--settings-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--settings-test-tab",
        choices=("agent", "model", "voice", "appearance"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--model-switch-test", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.data_dir:
        os.environ["HATTIP_LAB_DATA_DIR"] = str(args.data_dir.resolve())
    if args.check:
        return run_check(args.data_dir)

    enable_per_monitor_dpi()

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先运行 scripts\\setup.ps1", file=sys.stderr)
        return 2

    models = ModelLibrary(args.data_dir or default_data_dir())
    settings = SettingsStore(models.root / "settings.json")
    saved = settings.as_dict()
    provider_list = [HermesProvider()]
    default_provider = "hermes"
    if args.mock_hermes:
        provider_list.insert(0, MockProvider())
        default_provider = "mock"
    providers = ProviderRegistry(provider_list, default_id=default_provider)
    configure_remote_providers(providers, settings)
    voice_root = voice_data_root(models.root)
    tts_service = ResilientTtsService(
        voice_root / "tts-cache",
        resolve_piper_model(voice_root, saved.get("piper_model", "")),
        piper_runtime=PIPER_RUNTIME,
    )
    configure_tts_service(tts_service, settings, voice_root)
    scale = float(saved.get("scale", 1.0))
    if args.mock_hermes:
        settings.update(provider="mock")

    bridge = BridgeServer(
        registry=providers,
        model_library=models,
        static_root=WEB_DIR,
        tts_service=tts_service,
        port=args.port,
    )
    try:
        bridge.start()
    except OSError as exc:
        requested_port = str(args.port) if args.port else "自动端口"
        print(f"无法启动本地桥接（{requested_port}）：{exc}", file=sys.stderr)
        return 3

    api = DesktopApi(
        bridge.port,
        args.mode,
        args.provider,
        settings,
        providers,
        models,
        tts_service=tts_service,
        mock=args.mock_hermes,
    )
    hotkey = PushToTalkHotkey(api.on_push_to_talk)
    api._attach_hotkey(hotkey)
    x = saved.get("window_x") if isinstance(saved.get("window_x"), int) else None
    y = saved.get("window_y") if isinstance(saved.get("window_y"), int) else None
    window = webview.create_window(
        "HatTip Lab",
        url=f"http://127.0.0.1:{bridge.port}/ui/pet.html",
        js_api=api,
        width=round(BASE_SIZE[0] * scale),
        height=round(BASE_SIZE[1] * scale),
        x=x,
        y=y,
        min_size=(336, 448),
        resizable=False,
        frameless=True,
        easy_drag=False,
        shadow=False,
        on_top=bool(saved.get("on_top", True)),
        transparent=True,
        background_color="#000000",
        text_select=False,
        zoomable=False,
    )
    api._attach_window(window)
    api._create_settings_window(hidden=True)
    window.events.before_show += lambda *_: apply_windows_transparency(window, args.debug)

    def create_companion() -> None:
        if not window.events.loaded.wait(15):
            return
        api._restart_hotkey()
        try:
            bubble_window = WpfBubbleWindow(api)
            api._attach_bubble_window(bubble_window)
            window.run_js("window.enableExternalBubble?.();")
            if args.debug:
                print("[host] native_bubble_ready", flush=True)
        except Exception as exc:
            if args.debug:
                print(f"[host] bubble_window_error: {exc}", flush=True)
        try:
            api._attach_tray(NativeTrayIcon(api))
        except Exception as exc:
            if args.debug:
                print(f"[host] tray_icon_error: {exc}", flush=True)

    def remember_position(*_: Any) -> None:
        try:
            settings.update(window_x=int(window.x), window_y=int(window.y))
        except (TypeError, ValueError, AttributeError):
            pass

    window.events.moved += remember_position
    if args.debug:
        window.events.loaded += lambda *_: print("[host] page_loaded", flush=True)
    def close_companion(*_: Any) -> None:
        hotkey.stop()
        api._sovits_installer.stop()
        if api._tray is not None:
            api._tray.dispose()
            api._tray = None
        api._destroy_settings_window()
        api._destroy_bubble_window()

    window.events.closing += lambda *_: threading.Thread(
        target=close_companion, daemon=True
    ).start()
    window.events.closed += lambda *_: bridge.stop()
    if args.exit_after and args.exit_after > 0:
        window.events.shown += lambda *_: threading.Timer(args.exit_after, api.exit_app).start()
    if args.bubble_test:
        def show_test_bubble() -> None:
            if api._bubble_ready.wait(20):
                api.show_bubble(args.bubble_test, {"stay": args.bubble_test_stay})

        window.events.shown += lambda *_: threading.Thread(
            target=show_test_bubble, daemon=True
        ).start()
    if args.scale_test:
        def run_scale_test() -> None:
            if window.events.loaded.wait(20):
                threading.Event().wait(1.0)
                api.set_scale(args.scale_test)

        window.events.shown += lambda *_: threading.Thread(
            target=run_scale_test, daemon=True
        ).start()
    if args.layout_test:
        def report_layout_test() -> None:
            if window.events.loaded.wait(20):
                threading.Event().wait(3.0)
                result = window.evaluate_js("window.getPetDiagnostics?.()")
                print(f"[layout] {json.dumps(result, ensure_ascii=False)}", flush=True)

        window.events.shown += lambda *_: threading.Thread(
            target=report_layout_test, daemon=True
        ).start()
    if args.composer_test:
        def open_composer_test() -> None:
            if window.events.loaded.wait(20):
                threading.Event().wait(2.0)
                window.run_js("window.openPetComposer?.();")

        window.events.shown += lambda *_: threading.Thread(
            target=open_composer_test, daemon=True
        ).start()
    if args.settings_test:
        def open_settings_test() -> None:
            if window.events.loaded.wait(20):
                threading.Event().wait(2.0)
                api.open_settings()
                if args.settings_test_tab:
                    threading.Event().wait(2.0)
                    with api._settings_window_lock:
                        settings_window = api._settings_window
                    if settings_window is not None and settings_window.events.loaded.wait(10):
                        settings_window.run_js(
                            "document.querySelector('[data-tab=\""
                            + args.settings_test_tab
                            + "\"]')?.click();"
                        )

        window.events.shown += lambda *_: threading.Thread(
            target=open_settings_test, daemon=True
        ).start()
    if args.model_switch_test:
        def run_model_switch_test() -> None:
            if not window.events.loaded.wait(20):
                return
            threading.Event().wait(4.0)
            before = window.evaluate_js("window.getPetDiagnostics?.()")
            requested = api.set_model(args.model_switch_test)
            threading.Event().wait(5.0)
            after = window.evaluate_js("window.getPetDiagnostics?.()")
            print(
                f"[model-switch] {json.dumps({'requested': requested, 'before': before, 'after': after}, ensure_ascii=False)}",
                flush=True,
            )

        window.events.shown += lambda *_: threading.Thread(
            target=run_model_switch_test, daemon=True
        ).start()
    try:
        webview.start(create_companion, debug=args.debug, gui="edgechromium")
    finally:
        hotkey.stop()
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
