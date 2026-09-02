import datetime
import logging
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango
from ks_includes.screen_panel import ScreenPanel


DETECTION_TYPES = [
    {"key": "spaghetti",            "name": "意面检测", "macro": "AI_DETECT_SPAGHETTI",
     "default_threshold": 0.70, "default_interval": 30},
    {"key": "warphead",             "name": "翘头检测", "macro": "AI_DETECT_WARPHEAD",
     "default_threshold": 0.75, "default_interval": 60},
    {"key": "tooLessAndTooMuch",    "name": "挤出量检测", "macro": "AI_DETECT_EXTRUSION",
     "default_threshold": 0.70, "default_interval": 60},
    {"key": "warpEdgesAndNonStick", "name": "翘边检测", "macro": "AI_DETECT_NONSTICK",
     "default_threshold": 0.70, "default_interval": 120},
    {"key": "foreignBody",          "name": "异物检测", "macro": "AI_DETECT_FOREIGNBODY",
     "default_threshold": 0.60, "default_interval": 60},
    {"key": "coco80",               "name": "通用检测", "macro": "AI_DETECT_COCO80",
     "default_threshold": 0.50, "default_interval": 60},
]


class Panel(ScreenPanel):

    def __init__(self, screen, title):
        super().__init__(screen, title)
        self.menu = ['main_menu']
        self.settings = {}
        self.auto_detect_timers = {}
        self.status_timeout = None
        self.ai_online = False
        self.pause_on_defect = True
        self.last_result = None
        self._active = False

        self.labels['main_menu'] = self._build_main_page()
        self.labels['settings_menu'] = self._build_settings_page()

        self.content.add(self.labels['main_menu'])

    # ==================== UI Build ====================

    def _build_main_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # --- Status bar ---
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_row.get_style_context().add_class("frame-item")

        self.labels['status_label'] = Gtk.Label()
        self.labels['status_label'].set_markup(
            '<big><b>AI状态:</b> <span foreground="gray">● 检查中...</span></big>')
        self.labels['status_label'].set_hexpand(True)
        self.labels['status_label'].set_halign(Gtk.Align.START)

        settings_btn = self._gtk.Button("fine-tune", _("检测设置"), "color3")
        settings_btn.connect("clicked", self.load_menu, 'settings', _("检测设置"))

        status_row.pack_start(self.labels['status_label'], True, True, 5)
        status_row.pack_end(settings_btn, False, False, 5)
        page.pack_start(status_row, False, False, 0)

        # --- Manual detection buttons (3 columns x 2 rows) ---
        detect_grid = self._gtk.HomogeneousGrid()
        buttons_cfg = [
            ("spaghetti",            "意面", "color1", 0, 0),
            ("warphead",             "翘头", "color2", 1, 0),
            ("tooLessAndTooMuch",    "挤出量", "color3", 2, 0),
            ("warpEdgesAndNonStick", "翘边", "color1", 0, 1),
            ("foreignBody",          "异物", "color2", 1, 1),
            ("coco80",               "通用", "color3", 2, 1),
        ]
        for key, name, style, col, row in buttons_cfg:
            btn = self._gtk.Button(None, name, style)
            btn.connect("clicked", self.manual_detect, key)
            self.labels[f'btn_{key}'] = btn
            detect_grid.attach(btn, col, row, 1, 1)
        page.pack_start(detect_grid, True, True, 0)

        # --- Pause on defect toggle ---
        pause_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        pause_row.get_style_context().add_class("frame-item")

        pause_label = Gtk.Label()
        pause_label.set_markup("<big><b>缺陷暂停打印</b></big>")
        pause_label.set_hexpand(True)
        pause_label.set_halign(Gtk.Align.START)

        self.labels['pause_switch'] = Gtk.Switch()
        self.labels['pause_switch'].set_active(True)
        self.labels['pause_switch'].set_property("width-request", round(self._gtk.font_size * 7))
        self.labels['pause_switch'].set_property("height-request", round(self._gtk.font_size * 3.5))
        self.labels['pause_switch'].connect("notify::active", self.on_pause_toggled)

        pause_row.pack_start(pause_label, True, True, 5)
        pause_row.pack_end(self.labels['pause_switch'], False, False, 5)
        page.pack_start(pause_row, False, False, 0)

        # --- Latest detection result ---
        result_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        result_row.get_style_context().add_class("frame-item")

        result_title = Gtk.Label()
        result_title.set_markup("<big><b>最近检测结果</b></big>")
        result_title.set_halign(Gtk.Align.START)

        self.labels['result_text'] = Gtk.Label()
        self.labels['result_text'].set_markup("暂无检测结果")
        self.labels['result_text'].set_halign(Gtk.Align.START)
        self.labels['result_text'].set_line_wrap(True)
        self.labels['result_text'].set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)

        result_row.pack_start(result_title, False, False, 2)
        result_row.pack_start(self.labels['result_text'], False, False, 2)
        page.pack_start(result_row, False, False, 0)

        return page

    def _build_settings_page(self):
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        row_idx = 0

        for dt in DETECTION_TYPES:
            key = dt['key']

            # --- Section header ---
            header = Gtk.Label()
            header.set_markup(f"\n<big><b>{dt['name']}</b></big>  <small>({key})</small>")
            header.set_halign(Gtk.Align.START)
            grid.attach(header, 0, row_idx, 4, 1)
            row_idx += 1

            # --- Enable + Scheduled switches ---
            sw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            sw_row.get_style_context().add_class("frame-item")

            en_label = Gtk.Label()
            en_label.set_markup("<b>启用</b>")
            self.labels[f'{key}_enabled'] = Gtk.Switch()
            self.labels[f'{key}_enabled'].set_active(True)
            self.labels[f'{key}_enabled'].set_property("width-request", round(self._gtk.font_size * 5))
            self.labels[f'{key}_enabled'].set_property("height-request", round(self._gtk.font_size * 3))

            auto_label = Gtk.Label()
            auto_label.set_markup("<b>自动检测</b>")
            self.labels[f'{key}_scheduled'] = Gtk.Switch()
            self.labels[f'{key}_scheduled'].set_active(False)
            self.labels[f'{key}_scheduled'].set_property("width-request", round(self._gtk.font_size * 5))
            self.labels[f'{key}_scheduled'].set_property("height-request", round(self._gtk.font_size * 3))

            sw_row.pack_start(en_label, False, False, 5)
            sw_row.pack_start(self.labels[f'{key}_enabled'], False, False, 5)
            sw_row.pack_start(Gtk.Box(), True, True, 0)
            sw_row.pack_start(auto_label, False, False, 5)
            sw_row.pack_start(self.labels[f'{key}_scheduled'], False, False, 5)

            grid.attach(sw_row, 0, row_idx, 4, 1)
            row_idx += 1

            # --- Confidence threshold slider ---
            conf_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            conf_label = Gtk.Label()
            conf_label.set_markup("<b>置信度</b>")
            conf_label.set_size_request(round(self._gtk.font_size * 5), -1)

            self.labels[f'{key}_threshold'] = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 0.1, 1.0, 0.05)
            self.labels[f'{key}_threshold'].set_value(dt['default_threshold'])
            self.labels[f'{key}_threshold'].set_digits(2)
            self.labels[f'{key}_threshold'].set_hexpand(True)
            self.labels[f'{key}_threshold'].set_has_origin(True)
            self.labels[f'{key}_threshold'].get_style_context().add_class("option_slider")

            conf_row.pack_start(conf_label, False, False, 5)
            conf_row.pack_start(self.labels[f'{key}_threshold'], True, True, 5)
            grid.attach(conf_row, 0, row_idx, 4, 1)
            row_idx += 1

            # --- Detection interval slider ---
            int_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            int_label = Gtk.Label()
            int_label.set_markup("<b>间隔(s)</b>")
            int_label.set_size_request(round(self._gtk.font_size * 5), -1)

            self.labels[f'{key}_interval'] = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, 10, 300, 10)
            self.labels[f'{key}_interval'].set_value(dt['default_interval'])
            self.labels[f'{key}_interval'].set_digits(0)
            self.labels[f'{key}_interval'].set_hexpand(True)
            self.labels[f'{key}_interval'].set_has_origin(True)
            self.labels[f'{key}_interval'].get_style_context().add_class("option_slider")

            int_row.pack_start(int_label, False, False, 5)
            int_row.pack_start(self.labels[f'{key}_interval'], True, True, 5)
            grid.attach(int_row, 0, row_idx, 4, 1)
            row_idx += 1

        # --- Save button ---
        save_btn = self._gtk.Button(None, _("保存设置"), "color1")
        save_btn.connect("clicked", self.save_settings)
        grid.attach(save_btn, 1, row_idx, 2, 1)

        scroll = self._gtk.ScrolledWindow()
        scroll.add(grid)
        return scroll

    # ==================== Lifecycle ====================

    def activate(self):
        self._active = True
        self.fetch_status()
        self.fetch_settings()
        self.fetch_latest_result()
        self.status_timeout = GLib.timeout_add_seconds(30, self.fetch_status)

    def deactivate(self):
        self._active = False
        if self.status_timeout:
            GLib.source_remove(self.status_timeout)
            self.status_timeout = None
        self._stop_all_auto_detect()

    def back(self):
        if len(self.menu) > 1:
            self.unload_menu()
            return True
        return False

    def process_update(self, action, data):
        if action == "notify_ai_detection_result":
            if isinstance(data, dict):
                self.last_result = data
                self._update_result_display()

    # ==================== REST API ====================

    def fetch_status(self):
        if not self._active:
            return False
        def _do():
            try:
                result = self._screen.apiclient.send_request("server/ai_detection/status")
            except Exception as e:
                logging.exception(f"AI detection: failed to fetch status: {e}")
                result = None
            GLib.idle_add(self._on_status_fetched, result)
        threading.Thread(target=_do, daemon=True).start()
        return self._active

    def _on_status_fetched(self, result):
        try:
            if result and isinstance(result, dict) and 'result' in result:
                data = result['result']
                if not isinstance(data, dict):
                    return
                self.ai_online = bool(data.get('service_available', False))
                color = "green" if self.ai_online else "red"
                text = "在线" if self.ai_online else "离线"
                self.labels['status_label'].set_markup(
                    f'<big><b>AI状态:</b> <span foreground="{color}">● {text}</span></big>')
                if 'pause_on_defect' in data:
                    self._set_pause_switch(bool(data['pause_on_defect']))
                if 'last_detection' in data and isinstance(data['last_detection'], dict):
                    self.last_result = data['last_detection']
                    self._update_result_display()
            else:
                self.ai_online = False
                self.labels['status_label'].set_markup(
                    '<big><b>AI状态:</b> <span foreground="gray">● 未知</span></big>')
        except Exception as e:
            logging.exception(f"AI detection: error updating status UI: {e}")

    def fetch_settings(self):
        def _do():
            try:
                result = self._screen.apiclient.send_request("server/ai_detection/settings")
            except Exception as e:
                logging.exception(f"AI detection: failed to fetch settings: {e}")
                result = None
            GLib.idle_add(self._on_settings_fetched, result)
        threading.Thread(target=_do, daemon=True).start()

    def _on_settings_fetched(self, result):
        try:
            if not result or not isinstance(result, dict) or 'result' not in result:
                return
            data = result['result']
            if not isinstance(data, dict):
                return
            categories = data.get('categories', {})
            if not isinstance(categories, dict):
                return
            self.settings = categories

            if 'pause_on_defect' in data:
                self._set_pause_switch(bool(data['pause_on_defect']))

            for dt in DETECTION_TYPES:
                key = dt['key']
                if key not in categories:
                    continue
                cat = categories[key]
                if not isinstance(cat, dict):
                    continue
                if f'{key}_enabled' in self.labels:
                    self.labels[f'{key}_enabled'].set_active(bool(cat.get('enabled', True)))
                if f'{key}_scheduled' in self.labels:
                    self.labels[f'{key}_scheduled'].set_active(bool(cat.get('scheduled', False)))
                if f'{key}_threshold' in self.labels:
                    try:
                        val = float(cat.get('confidence_threshold', dt['default_threshold']))
                        self.labels[f'{key}_threshold'].set_value(max(0.1, min(1.0, val)))
                    except (TypeError, ValueError):
                        pass
                if f'{key}_interval' in self.labels:
                    try:
                        val = float(cat.get('detection_interval', dt['default_interval']))
                        self.labels[f'{key}_interval'].set_value(max(10, min(300, val)))
                    except (TypeError, ValueError):
                        pass

            self._update_auto_detect_timers(categories)
        except Exception as e:
            logging.exception(f"AI detection: error updating settings UI: {e}")

    def fetch_latest_result(self):
        def _do():
            try:
                result = self._screen.apiclient.send_request("server/ai_detection/history?limit=1")
            except Exception as e:
                logging.exception(f"AI detection: failed to fetch history: {e}")
                result = None
            GLib.idle_add(self._on_history_fetched, result)
        threading.Thread(target=_do, daemon=True).start()

    def _on_history_fetched(self, result):
        try:
            if not result or not isinstance(result, dict) or 'result' not in result:
                return
            data = result['result']
            if not isinstance(data, dict):
                return
            records = data.get('records', [])
            if isinstance(records, list) and records:
                self.last_result = records[0]
                self._update_result_display()
        except Exception as e:
            logging.exception(f"AI detection: error updating history UI: {e}")

    def _update_result_display(self):
        if not self.last_result or not isinstance(self.last_result, dict):
            self.labels['result_text'].set_markup("暂无检测结果")
            return
        r = self.last_result
        # model_name or defect_type
        dtype = str(r.get('model_name', r.get('defect_type', '未知')))
        # confidence: top-level or from detections[0]
        conf_str = "N/A"
        try:
            if 'confidence' in r:
                conf_str = f"{float(r['confidence']):.2f}"
            else:
                detections = r.get('detections', [])
                if isinstance(detections, list) and detections:
                    conf_str = f"{float(detections[0].get('confidence', 0)):.2f}"
        except (TypeError, ValueError, IndexError):
            pass
        defect = bool(r.get('has_defect', False))
        # timestamp: unix float or string
        ts_raw = r.get('timestamp', '')
        try:
            ts = datetime.datetime.fromtimestamp(float(ts_raw)).strftime('%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError, OSError):
            ts = str(ts_raw)
        status = '<span foreground="red">异常</span>' if defect else '<span foreground="green">正常</span>'
        self.labels['result_text'].set_markup(
            f"类型: <b>{dtype}</b> | 置信度: <b>{conf_str}</b> | {status}\n"
            f"时间: {ts}")

    # ==================== User Actions ====================

    def manual_detect(self, widget, defect_type):
        if not self.ai_online:
            self._screen.show_popup_message(_("AI 服务离线，无法检测"), level=2)
            return

        dt_info = next((d for d in DETECTION_TYPES if d['key'] == defect_type), None)
        if not dt_info:
            logging.error(f"AI detection: unknown defect type: {defect_type}")
            return
        macro = dt_info['macro']
        if macro not in self._printer.get_gcode_macros():
            logging.warning(f"AI detection: macro not found: {macro}")
            self._screen.show_popup_message(
                f"宏 {macro} 未定义，请检查 ai_setting.cfg 是否已加载", level=2)
            return
        self._screen._ws.klippy.gcode_script(macro)
        self._screen.show_popup_message(f"{dt_info['name']}已触发", level=1)

    def on_pause_toggled(self, switch, gparam):
        active = switch.get_active()

        def _do():
            try:
                self._screen.apiclient.post_request(
                    "server/ai_detection/settings",
                    json={"pause_on_defect": active})
            except Exception as e:
                logging.exception(f"AI detection: failed to update pause_on_defect: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def save_settings(self, widget):
        categories = {}
        for dt in DETECTION_TYPES:
            key = dt['key']
            categories[key] = {
                "enabled": self.labels[f'{key}_enabled'].get_active(),
                "confidence_threshold": round(self.labels[f'{key}_threshold'].get_value(), 2),
                "detection_interval": int(self.labels[f'{key}_interval'].get_value()),
                "scheduled": self.labels[f'{key}_scheduled'].get_active(),
            }

        def _do():
            try:
                result = self._screen.apiclient.post_request(
                    "server/ai_detection/settings",
                    json={"categories": categories})
                if result:
                    GLib.idle_add(self._screen.show_popup_message, _("设置已保存"), 1)
                    GLib.idle_add(self._update_auto_detect_timers, categories)
                else:
                    GLib.idle_add(self._screen.show_popup_message, _("保存失败"), 2)
            except Exception as e:
                logging.exception(f"AI detection: failed to save settings: {e}")
                GLib.idle_add(self._screen.show_popup_message, _("保存失败"), 2)
        threading.Thread(target=_do, daemon=True).start()

    # ==================== Helpers ====================

    def _set_pause_switch(self, active):
        self.pause_on_defect = active
        try:
            self.labels['pause_switch'].handler_block_by_func(self.on_pause_toggled)
            self.labels['pause_switch'].set_active(active)
            self.labels['pause_switch'].handler_unblock_by_func(self.on_pause_toggled)
        except Exception as e:
            logging.warning(f"AI detection: failed to update pause switch: {e}")

    # ==================== Auto Detection Timers ====================

    def _update_auto_detect_timers(self, categories):
        self._stop_all_auto_detect()
        if not isinstance(categories, dict):
            return
        for dt in DETECTION_TYPES:
            key = dt['key']
            cat = categories.get(key, {})
            if not isinstance(cat, dict):
                continue
            if cat.get('enabled') and cat.get('scheduled'):
                try:
                    interval = max(10, int(cat.get('detection_interval', dt['default_interval'])))
                except (TypeError, ValueError):
                    interval = dt['default_interval']
                self.auto_detect_timers[key] = GLib.timeout_add_seconds(
                    interval, self._auto_detect_tick, key)
                logging.info(f"AI auto-detect scheduled: {key} every {interval}s")

    def _auto_detect_tick(self, defect_type):
        if not self._active:
            return False
        try:
            state = self._printer.get_stat("print_stats", "state")
            if state == "printing":
                self.manual_detect(None, defect_type)
        except Exception as e:
            logging.exception(f"AI detection: auto-detect tick error for {defect_type}: {e}")
        return True

    def _stop_all_auto_detect(self):
        for key, timer_id in self.auto_detect_timers.items():
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
            logging.info(f"AI auto-detect stopped: {key}")
        self.auto_detect_timers.clear()
