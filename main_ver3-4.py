import os
import time
import threading
import json
import datetime
import webbrowser
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from lxml import etree
from google import genai
from google.genai import types
import customtkinter as ctk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_SUPPORTED = True
except ImportError:
    DND_SUPPORTED = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

if DND_SUPPORTED:
    class ModernTk(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class ModernTk(ctk.CTk):
        pass

class UltimateFB2Translator:
    def __init__(self, root):
        self.root = root
        self.root.title("Stable FB2 AI Translator")
        self.root.geometry("950x950")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.is_running = False
        self.is_paused = False
        self.file_queue = []
        self.req_count = 0
        
        self.char_limit = 10000
        self.current_lang = "Українська"
        self.saved_settings = {}
        
        if getattr(sys, 'frozen', False):
            bundle_dir = sys._MEIPASS
        else:
            bundle_dir = os.path.dirname(os.path.abspath(__file__))

        documents_dir = os.path.join(os.path.expanduser("~"), "Documents")
        self.session_file = os.path.join(documents_dir, "session.json")
        self.locales_file = os.path.join(bundle_dir, "locales.json")
        
        self.api_key = ""

        self.init_locales()
        self.load_session()
        self.create_widgets()
        self.update_timer()

    def init_locales(self):
        if not os.path.exists(self.locales_file):
            messagebox.showerror("Помилка", f"Файл локалізацій '{self.locales_file}' не знайдено.")
            self.root.destroy()
            sys.exit()

        try:
            with open(self.locales_file, "r", encoding="utf-8") as f:
                self.locales = json.load(f)
        except Exception as e:
            messagebox.showerror("Помилка", f"Не вдалося прочитати файл '{self.locales_file}': {e}")
            self.root.destroy()
            sys.exit()
            
        if not self.locales:
            messagebox.showerror("Помилка", f"Файл '{self.locales_file}' порожній.")
            self.root.destroy()
            sys.exit()

    def load_session(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if os.path.exists(self.session_file):
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        self.req_count = data.get("requests", 0)
                    self.file_queue = data.get("queue", [])
                    self.current_lang = data.get("language", "Українська")
                    self.saved_settings = data.get("settings", {})
            except Exception:
                pass
        
        if self.current_lang not in self.locales:
            self.current_lang = list(self.locales.keys())[0]

    def save_session(self):
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        settings_dict = self.saved_settings
        if hasattr(self, 'api_key_var'):
            settings_dict = {
                "api_key": self.api_key_var.get(),
                "char_limit": self.char_limit_var.get(),
                "auto_pause": self.auto_pause_var.get(),
                "model": self.model_var.get(),
                "sys_prompt": self.sys_prompt_text.get("0.0", "end").strip(),
                "prompt_1": self.prefix1_text.get("0.0", "end").strip(),
                "prompt_2": self.prefix2_text.get("0.0", "end").strip(),
                "prompt_3": self.prefix3_text.get("0.0", "end").strip()
            }
            for key, slider in self.sliders.items():
                settings_dict[key] = slider.get()
                
        data = {
            "date": today,
            "requests": self.req_count,
            "queue": self.file_queue,
            "language": self.current_lang,
            "settings": settings_dict
        }
        try:
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def on_closing(self):
        if self.file_queue:
            loc = self.locales[self.current_lang]
            if not messagebox.askyesno(loc.get("msg_exit_title", "Exit"), loc.get("msg_exit_text", "Exit?")):
                return
        self.save_session()
        self.root.destroy()

    def create_widgets(self):
        loc = self.locales[self.current_lang]
        loaded_key = self.saved_settings.get("api_key", self.api_key)
        self.api_key_var = tk.StringVar(value=loaded_key)
        
        loaded_limit = self.saved_settings.get("char_limit", str(self.char_limit))
        self.char_limit_var = tk.StringVar(value=str(loaded_limit))

        frame_header = ctk.CTkFrame(self.root, fg_color="transparent")
        frame_header.pack(fill=tk.X, padx=10, pady=5)
        
        self.btn_theme = ctk.CTkButton(frame_header, text=loc.get("btn_theme", "Theme"), width=110, command=self.toggle_theme)
        self.btn_theme.pack(side=tk.LEFT, padx=5)

        self.lbl_counter = ctk.CTkLabel(frame_header, text=loc.get("req_today", "{}").format(self.req_count), font=("TkDefaultFont", 12, "bold"))
        self.lbl_counter.pack(side=tk.LEFT, padx=15)
        
        self.lang_var = tk.StringVar(value=self.current_lang)
        lang_combo = ctk.CTkComboBox(frame_header, variable=self.lang_var, values=list(self.locales.keys()), command=self.change_language, width=120)
        lang_combo.pack(side=tk.RIGHT, padx=5)

        self.lbl_timer = ctk.CTkLabel(frame_header, text=loc.get("reset_in", "{}").format("--:--:--", "--:--"), text_color="#2FA572")
        self.lbl_timer.pack(side=tk.RIGHT, padx=15)

        frame_top = ctk.CTkFrame(self.root, fg_color="transparent")
        frame_top.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.btn_api = ctk.CTkButton(frame_top, text=f"▶ {loc.get('panel_api', 'API')}", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_api, self.btn_api, "panel_api"))
        self.btn_api.pack(fill=tk.X)
        self.frame_api = ctk.CTkFrame(frame_top)
        
        ctk.CTkEntry(self.frame_api, textvariable=self.api_key_var, width=500).pack(padx=10, pady=10, side=tk.LEFT)
        ctk.CTkButton(self.frame_api, text="?", width=30, command=self.show_api_help).pack(padx=5, pady=10, side=tk.LEFT)

        self.btn_settings = ctk.CTkButton(frame_top, text=f"▶ {loc.get('panel_settings', 'Settings')}", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_settings, self.btn_settings, "panel_settings"))
        self.btn_settings.pack(fill=tk.X, pady=(5, 0))
        self.frame_settings = ctk.CTkFrame(frame_top)

        self.lbl_char_limit = ctk.CTkLabel(self.frame_settings, text=loc.get("char_limit", "Limit:"))
        self.lbl_char_limit.grid(row=0, column=0, sticky='w', padx=10, pady=5)
        ctk.CTkEntry(self.frame_settings, textvariable=self.char_limit_var, width=100).grid(row=0, column=1, sticky='w', padx=10, pady=5)

        self.sliders = {}
        self.lbl_settings = {}
        settings_configs = [
            ("temp", "temperature", 1.0, 0.0, 1.0, 10, True),
            ("delay_base", "delay_req", 2, 1, 20, 19, False),
            ("delay_protect", "delay_protect", 2, 1, 20, 19, False),
            ("delay_json", "delay_json", 2, 1, 20, 19, False),
            ("delay_mismatch", "delay_mismatch", 2, 1, 20, 19, False),
            ("delay_error", "delay_error", 2, 1, 20, 19, False)
        ]

        for i, (loc_key, var_key, default_val, min_v, max_v, steps, is_float) in enumerate(settings_configs, start=1):
            lbl_name = ctk.CTkLabel(self.frame_settings, text=loc.get(loc_key, loc_key))
            lbl_name.grid(row=i, column=0, sticky='w', padx=10, pady=2)
            self.lbl_settings[loc_key] = lbl_name
            
            saved_val = self.saved_settings.get(var_key, default_val)
            val_lbl = ctk.CTkLabel(self.frame_settings, text=str(saved_val), width=30)
            val_lbl.grid(row=i, column=2, sticky='w', padx=5, pady=2)

            def make_cmd(lbl, float_flag):
                if float_flag:
                    return lambda v, l=lbl: l.configure(text=f"{float(v):.1f}")
                return lambda v, l=lbl: l.configure(text=str(int(float(v))))

            slider = ctk.CTkSlider(self.frame_settings, from_=min_v, to=max_v, number_of_steps=steps, command=make_cmd(val_lbl, is_float))
            slider.set(saved_val)
            slider.grid(row=i, column=1, sticky='w', padx=10, pady=2)
            self.sliders[var_key] = slider

        self.auto_pause_var = tk.BooleanVar(value=self.saved_settings.get("auto_pause", False))
        self.switch_auto_pause = ctk.CTkSwitch(self.frame_settings, text=loc.get("auto_pause", "Auto-pause"), variable=self.auto_pause_var)
        self.switch_auto_pause.grid(row=len(settings_configs)+1, column=0, columnspan=2, sticky='w', padx=10, pady=10)

        self.btn_prompts = ctk.CTkButton(frame_top, text=f"▶ {loc.get('panel_prompts', 'Prompts')}", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_prompts, self.btn_prompts, "panel_prompts"))
        self.btn_prompts.pack(fill=tk.X, pady=(5, 0))
        self.frame_prompts = ctk.CTkFrame(frame_top)

        self.lbl_model = ctk.CTkLabel(self.frame_prompts, text=loc.get("model", "Model:"))
        self.lbl_model.grid(row=0, column=0, sticky='w', padx=10, pady=5)
        
        saved_model = self.saved_settings.get("model", "models/gemini-3.1-flash-lite")
        self.model_var = tk.StringVar(value=saved_model)
        model_combo = ctk.CTkComboBox(self.frame_prompts, variable=self.model_var, values=["models/gemini-3.1-flash-lite", "models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-2.0-flash"], width=300)
        model_combo.grid(row=0, column=1, sticky='w', padx=10, pady=5)

        self.lbl_sys_prompt = ctk.CTkLabel(self.frame_prompts, text=loc.get("sys_prompt", "Sys:"))
        self.lbl_sys_prompt.grid(row=1, column=0, sticky='nw', padx=10, pady=5)
        self.sys_prompt_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=45)
        self.sys_prompt_text.insert("0.0", self.saved_settings.get("sys_prompt", loc.get("def_sys", "")))
        self.sys_prompt_text.grid(row=1, column=1, sticky='w', padx=10, pady=5)

        self.lbl_p1 = ctk.CTkLabel(self.frame_prompts, text=loc.get("prompt_1", "P1:"))
        self.lbl_p1.grid(row=2, column=0, sticky='nw', padx=10, pady=5)
        self.prefix1_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=45)
        self.prefix1_text.insert("0.0", self.saved_settings.get("prompt_1", loc.get("def_p1", "")))
        self.prefix1_text.grid(row=2, column=1, sticky='w', padx=10, pady=5)

        self.lbl_p2 = ctk.CTkLabel(self.frame_prompts, text=loc.get("prompt_2", "P2:"))
        self.lbl_p2.grid(row=3, column=0, sticky='nw', padx=10, pady=5)
        self.prefix2_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=45)
        self.prefix2_text.insert("0.0", self.saved_settings.get("prompt_2", loc.get("def_p2", "")))
        self.prefix2_text.grid(row=3, column=1, sticky='w', padx=10, pady=5)

        self.lbl_p3 = ctk.CTkLabel(self.frame_prompts, text=loc.get("prompt_3", "P3:"))
        self.lbl_p3.grid(row=4, column=0, sticky='nw', padx=10, pady=5)
        self.prefix3_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=45)
        self.prefix3_text.insert("0.0", self.saved_settings.get("prompt_3", loc.get("def_p3", "")))
        self.prefix3_text.grid(row=4, column=1, sticky='w', padx=10, pady=5)

        self.btn_queue = ctk.CTkButton(frame_top, text=f"▼ {loc.get('panel_queue', 'Queue')}", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_queue, self.btn_queue, "panel_queue", expand=True))
        self.btn_queue.pack(fill=tk.X, pady=(5, 0))
        self.frame_queue = ctk.CTkFrame(frame_top, fg_color="transparent")
        self.frame_queue.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        self.listbox_queue = tk.Listbox(self.frame_queue, selectmode=tk.EXTENDED, bg="#2b2b2b", fg="white", selectbackground="#1f538d", highlightthickness=0, relief="flat", font=("TkDefaultFont", 11))
        self.listbox_queue.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        for f in self.file_queue:
            self.listbox_queue.insert(tk.END, os.path.basename(f))
            
        if self.file_queue:
            self.root.after(100, lambda: self.btn_start.configure(state="normal"))

        if DND_SUPPORTED:
            self.listbox_queue.drop_target_register(DND_FILES)
            self.listbox_queue.dnd_bind('<<Drop>>', self.handle_drop)

        frame_q_btns = ctk.CTkFrame(self.frame_queue, fg_color="transparent")
        frame_q_btns.pack(pady=5)
        
        self.btn_add_files = ctk.CTkButton(frame_q_btns, text=loc.get("btn_add", "Add"), command=self.add_files, width=120)
        self.btn_add_files.pack(side=tk.LEFT, padx=5)
        self.btn_remove = ctk.CTkButton(frame_q_btns, text=loc.get("btn_remove", "Remove"), command=self.remove_selected, width=120)
        self.btn_remove.pack(side=tk.LEFT, padx=5)
        self.btn_up = ctk.CTkButton(frame_q_btns, text=loc.get("btn_up", "Up"), command=self.move_up, width=80)
        self.btn_up.pack(side=tk.LEFT, padx=5)
        self.btn_down = ctk.CTkButton(frame_q_btns, text=loc.get("btn_down", "Down"), command=self.move_down, width=80)
        self.btn_down.pack(side=tk.LEFT, padx=5)

        self.frame_controls = ctk.CTkFrame(frame_top, fg_color="transparent")
        self.frame_controls.pack(pady=10)

        self.btn_start = ctk.CTkButton(self.frame_controls, text=loc.get("btn_start", "Start"), command=self.start_process, width=150, state="disabled", fg_color="#2FA572", hover_color="#106A43")
        self.btn_start.pack(side=tk.LEFT, padx=5)

        self.btn_pause = ctk.CTkButton(self.frame_controls, text=loc.get("btn_pause", "Pause"), command=self.toggle_pause, width=150, state="disabled", fg_color="#C25A24", hover_color="#A04111")
        self.btn_pause.pack(side=tk.LEFT, padx=5)

        self.btn_console = ctk.CTkButton(frame_top, text=f"▼ {loc.get('panel_console', 'Console')}", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_console, self.btn_console, "panel_console", expand=True))
        self.btn_console.pack(fill=tk.X, pady=(5, 0))
        self.frame_console = ctk.CTkFrame(frame_top, fg_color="transparent")
        self.frame_console.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        self.log_area = ctk.CTkTextbox(self.frame_console, height=120)
        self.log_area.pack(pady=5, fill=tk.BOTH, expand=True)

    def change_language(self, choice):
        self.current_lang = choice
        loc = self.locales[self.current_lang]

        self.btn_theme.configure(text=loc.get("btn_theme", "Theme"))
        self.lbl_char_limit.configure(text=loc.get("char_limit", "Limit"))
        self.switch_auto_pause.configure(text=loc.get("auto_pause", "Auto-pause"))

        for loc_key, lbl in self.lbl_settings.items():
            lbl.configure(text=loc.get(loc_key, loc_key))

        self.lbl_model.configure(text=loc.get("model", "Model"))
        self.lbl_sys_prompt.configure(text=loc.get("sys_prompt", "Sys"))
        self.lbl_p1.configure(text=loc.get("prompt_1", "P1"))
        self.lbl_p2.configure(text=loc.get("prompt_2", "P2"))
        self.lbl_p3.configure(text=loc.get("prompt_3", "P3"))

        self.btn_add_files.configure(text=loc.get("btn_add", "Add"))
        self.btn_remove.configure(text=loc.get("btn_remove", "Remove"))
        self.btn_up.configure(text=loc.get("btn_up", "Up"))
        self.btn_down.configure(text=loc.get("btn_down", "Down"))

        self.btn_start.configure(text=loc.get("btn_start", "Start"))
        self.btn_pause.configure(text=loc.get("btn_resume", "Resume") if self.is_paused else loc.get("btn_pause", "Pause"))

        self.sys_prompt_text.delete("0.0", "end")
        self.sys_prompt_text.insert("0.0", loc.get("def_sys", ""))

        self.prefix1_text.delete("0.0", "end")
        self.prefix1_text.insert("0.0", loc.get("def_p1", ""))

        self.prefix2_text.delete("0.0", "end")
        self.prefix2_text.insert("0.0", loc.get("def_p2", ""))

        self.prefix3_text.delete("0.0", "end")
        self.prefix3_text.insert("0.0", loc.get("def_p3", ""))

        self.update_toggle_button_text(self.frame_api, self.btn_api, loc.get("panel_api", "API"))
        self.update_toggle_button_text(self.frame_settings, self.btn_settings, loc.get("panel_settings", "Settings"))
        self.update_toggle_button_text(self.frame_prompts, self.btn_prompts, loc.get("panel_prompts", "Prompts"))
        self.update_toggle_button_text(self.frame_queue, self.btn_queue, loc.get("panel_queue", "Queue"))
        self.update_toggle_button_text(self.frame_console, self.btn_console, loc.get("panel_console", "Console"))

        self.save_session()
        
    def update_toggle_button_text(self, frame, btn, text):
        if frame.winfo_ismapped():
            btn.configure(text=f"▼ {text}")
        else:
            btn.configure(text=f"▶ {text}")

    def toggle_theme(self):
        if ctk.get_appearance_mode() == "Dark":
            ctk.set_appearance_mode("Light")
            self.listbox_queue.config(bg="#f0f0f0", fg="black", selectbackground="#3a7ebf")
        else:
            ctk.set_appearance_mode("Dark")
            self.listbox_queue.config(bg="#2b2b2b", fg="white", selectbackground="#1f538d")

    def show_api_help(self):
        loc = self.locales[self.current_lang]
        help_win = ctk.CTkToplevel(self.root)
        help_win.title(loc.get("help_title", "Help"))
        help_win.geometry("450x150")
        help_win.attributes("-topmost", True)
        
        ctk.CTkLabel(help_win, text=loc.get("help_text", "Text"), wraplength=400).pack(pady=20)
        
        def open_link():
            webbrowser.open("https://aistudio.google.com/app/apikey")
            
        ctk.CTkButton(help_win, text=loc.get("help_btn", "Open"), command=open_link).pack(pady=10)

    def update_timer(self):
        loc = self.locales[self.current_lang]
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        reset_utc = now_utc.replace(hour=17, minute=0, second=0, microsecond=0)
        if now_utc >= reset_utc:
            reset_utc += datetime.timedelta(days=1)
        
        diff = reset_utc - now_utc
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        local_reset = reset_utc.astimezone()
        local_time_str = local_reset.strftime("%H:%M")
        
        time_format = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        self.lbl_timer.configure(text=loc.get("reset_in", "{}").format(time_format, local_time_str))
        self.lbl_counter.configure(text=loc.get("req_today", "{}").format(self.req_count))
        
        self.root.after(1000, self.update_timer)

    def toggle_panel(self, frame, btn, loc_key, expand=False):
        loc = self.locales[self.current_lang]
        text = loc.get(loc_key, loc_key)
        if frame.winfo_ismapped():
            frame.pack_forget()
            btn.configure(text=f"▶ {text}")
        else:
            if expand:
                frame.pack(fill=tk.BOTH, expand=True, after=btn, padx=10, pady=(0, 5))
            else:
                frame.pack(fill=tk.X, after=btn, padx=10, pady=(0, 5))
            btn.configure(text=f"▼ {text}")

    def safe_log(self, msg):
        self.root.after(0, lambda: self._update_log(msg))

    def _update_log(self, msg):
        self.log_area.insert("end", f"{msg}\n")
        self.log_area.see("end")

    def handle_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        for f in files:
            if f.lower().endswith('.fb2') and f not in self.file_queue:
                self.file_queue.append(f)
                self.listbox_queue.insert(tk.END, os.path.basename(f))
        if self.file_queue:
            self.btn_start.configure(state="normal")
        self.save_session()

    def add_files(self):
        files = filedialog.askopenfilenames(filetypes=[("fb2 files", "*.fb2")])
        for f in files:
            if f not in self.file_queue:
                self.file_queue.append(f)
                self.listbox_queue.insert(tk.END, os.path.basename(f))
        if self.file_queue:
            self.btn_start.configure(state="normal")
        self.save_session()

    def remove_selected(self):
        selected_indices = list(self.listbox_queue.curselection())
        selected_indices.reverse()
        for idx in selected_indices:
            self.listbox_queue.delete(idx)
            self.file_queue.pop(idx)
        if not self.file_queue:
            self.btn_start.configure(state="disabled")
        self.save_session()

    def move_up(self):
        selected = self.listbox_queue.curselection()
        if not selected: return
        for pos in selected:
            if pos == 0: continue
            text = self.listbox_queue.get(pos)
            self.listbox_queue.delete(pos)
            self.listbox_queue.insert(pos - 1, text)
            
            self.file_queue[pos], self.file_queue[pos - 1] = self.file_queue[pos - 1], self.file_queue[pos]
            self.listbox_queue.selection_set(pos - 1)
        self.save_session()

    def move_down(self):
        selected = self.listbox_queue.curselection()
        if not selected: return
        for pos in reversed(selected):
            if pos == self.listbox_queue.size() - 1: continue
            text = self.listbox_queue.get(pos)
            self.listbox_queue.delete(pos)
            self.listbox_queue.insert(pos + 1, text)
            
            self.file_queue[pos], self.file_queue[pos + 1] = self.file_queue[pos + 1], self.file_queue[pos]
            self.listbox_queue.selection_set(pos + 1)
        self.save_session()

    def toggle_pause(self):
        loc = self.locales[self.current_lang]
        self.is_paused = not self.is_paused
        btn_text = loc.get("btn_resume", "Resume") if self.is_paused else loc.get("btn_pause", "Pause")
        self.btn_pause.configure(text=btn_text)
        self.save_session()

    def start_process(self):
        self.save_session()
        if not self.is_running and self.file_queue:
            self.is_running = True
            self.btn_start.configure(state="disabled")
            self.btn_pause.configure(state="normal")
            threading.Thread(target=self.run_translation, daemon=True).start()

    def smart_wait(self, seconds):
        elapsed = 0
        while elapsed < seconds and self.is_running:
            while self.is_paused and self.is_running:
                time.sleep(0.5)
            time.sleep(0.5)
            elapsed += 0.5

    def run_translation(self):
        loc = self.locales[self.current_lang]
        try:
            response_schema = {
                'type': 'OBJECT',
                'properties': {
                    'translations': {
                        'type': 'ARRAY',
                        'items': {'type': 'STRING'}
                    }
                },
                'required': ['translations']
            }
            
            while self.file_queue and self.is_running:
                while self.is_paused:
                    time.sleep(1)
                    
                input_path = self.file_queue[0]
                path_parts = os.path.splitext(input_path)
                output_path = f"{path_parts[0]}_translated{path_parts[1]}"
                
                file_to_load = output_path if os.path.exists(output_path) else input_path
                self.safe_log(loc.get("log_start", "---").format(os.path.basename(file_to_load)))
                
                try:
                    parser = etree.XMLParser(recover=True)
                    tree = etree.parse(file_to_load, parser)
                    
                    elements = tree.xpath('//*[local-name()="body"]//*[local-name()="p" or local-name()="v" or local-name()="title"]')
                    
                    total = len(elements)
                    idx = 0
                    reset_limit = True
                    dynamic_limit = 6000
                    
                    while idx < total and self.is_running:
                        while self.is_paused:
                            time.sleep(1)

                        if reset_limit:
                            try:
                                dynamic_limit = int(self.char_limit_var.get())
                            except ValueError:
                                dynamic_limit = 6000
                            reset_limit = False

                        batch_nodes = []
                        batch_texts = []
                        current_chars = 0
                        temp_idx = idx
                        
                        while temp_idx < total:
                            node = elements[temp_idx]
                            
                            if node.get("translated") in ["1", "error"]:
                                temp_idx += 1
                                if not batch_nodes:
                                    idx = temp_idx
                                continue

                            text = "".join(node.itertext()).strip()
                            if text:
                                if batch_nodes and current_chars + len(text) > dynamic_limit:
                                    break
                                batch_nodes.append(node)
                                batch_texts.append(text)
                                current_chars += len(text)
                            
                            temp_idx += 1

                        if not batch_texts:
                            idx = temp_idx
                            continue

                        self.safe_log(loc.get("log_batch", "{}").format(idx, temp_idx, total, dynamic_limit))
                        
                        success = False
                        retries = 0

                        while not success and retries < 3 and self.is_running:
                            
                            while self.is_paused and self.is_running:
                                time.sleep(0.5)
                                
                            current_api_key = self.api_key_var.get().strip()
                            if not current_api_key:
                                self.safe_log(loc.get("log_empty_key", "Error API"))
                                self.smart_wait(5)
                                continue

                            client = genai.Client(api_key=current_api_key)
                            selected_model = self.model_var.get().strip()
                            sys_instruct = self.sys_prompt_text.get("0.0", "end").strip()

                            if retries == 0:
                                prefix = self.prefix1_text.get("0.0", "end").strip()
                            elif retries == 1:
                                prefix = self.prefix2_text.get("0.0", "end").strip()
                            else:
                                prefix = self.prefix3_text.get("0.0", "end").strip()

                            try:
                                response = client.models.generate_content(
                                    model=selected_model,
                                    contents=f"{prefix} {json.dumps(batch_texts, ensure_ascii=False)}",
                                    config={
                                        'temperature': float(self.sliders["temperature"].get()),
                                        'response_mime_type': 'application/json',
                                        'response_schema': response_schema,
                                        'system_instruction': sys_instruct,
                                        'safety_settings': [
                                            types.SafetySetting(
                                                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                                                threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                            ),
                                            types.SafetySetting(
                                                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                                                threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                            ),
                                            types.SafetySetting(
                                                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                                                threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                            ),
                                            types.SafetySetting(
                                                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                                                threshold=types.HarmBlockThreshold.BLOCK_NONE,
                                            )
                                        ]
                                    }
                                )
                                
                                self.req_count += 1
                                self.save_session()
                                
                                if response.text is None:
                                    self.safe_log(loc.get("log_protect", "{}").format(retries + 1))
                                    self.smart_wait(int(float(self.sliders["delay_protect"].get())))
                                    retries += 1
                                    continue
                                
                                try:
                                    data = json.loads(response.text)
                                    translated = data.get("translations", [])
                                except (json.JSONDecodeError, TypeError):
                                    self.safe_log(loc.get("log_json_err", "{}").format(int(float(self.sliders['delay_json'].get()))))
                                    self.smart_wait(int(float(self.sliders["delay_json"].get())))
                                    retries += 1
                                    continue

                                if len(translated) == len(batch_nodes):
                                    for i, node in enumerate(batch_nodes):
                                        for child in list(node): node.remove(child)
                                        node.text = translated[i]
                                        node.set("translated", "1")
                                    success = True
                                else:
                                    self.safe_log(loc.get("log_mismatch", "{}").format(len(translated), len(batch_nodes), retries + 1))
                                    self.smart_wait(int(float(self.sliders["delay_mismatch"].get())))
                                    retries += 1
                                    
                            except Exception as e:
                                retries += 1
                                err_msg = str(e)
                                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                                    self.safe_log(loc.get("log_429", "429 Error"))
                                    if self.auto_pause_var.get():
                                        self.is_paused = True
                                        self.root.after(0, lambda: self.btn_pause.configure(text=loc.get("btn_resume", "Resume")))
                                        self.safe_log(loc.get("log_auto_paused", "Auto-paused"))
                                else:
                                    self.safe_log(loc.get("log_req_fail", "{}").format(err_msg[:80]))
                                self.smart_wait(int(float(self.sliders["delay_error"].get())))

                        if not success:
                            if len(batch_nodes) > 1:
                                dynamic_limit = max(100, current_chars // 2)
                                self.safe_log(loc.get("log_split", "{}").format(dynamic_limit))
                                reset_limit = False 
                            else:
                                self.safe_log(loc.get("log_block", "Block"))
                                batch_nodes[0].set("translated", "error")
                                idx = temp_idx
                                reset_limit = True
                                tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        else:
                            idx = temp_idx
                            reset_limit = True
                            tree.write(output_path, encoding='utf-8', xml_declaration=True)
                            self.smart_wait(int(float(self.sliders["delay_req"].get())))

                except Exception as file_error:
                    self.safe_log(loc.get("log_file_err", "{}").format(os.path.basename(input_path), file_error))
                
                if self.is_running:
                    self.safe_log(loc.get("log_finish", "{}").format(os.path.basename(input_path)))
                    self.file_queue.pop(0)
                    self.root.after(0, lambda: self.listbox_queue.delete(0))
                    self.save_session()

            if self.is_running:
                self.root.after(0, lambda: messagebox.showinfo(loc.get("msg_done_title", "Done"), loc.get("msg_done_text", "Done")))

        except Exception as e:
            self.safe_log(f"Error: {e}")
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.btn_start.configure(state="normal" if self.file_queue else "disabled"))
            self.root.after(0, lambda: self.btn_pause.configure(state="disabled"))

if __name__ == "__main__":
    if DND_SUPPORTED:
        root = ModernTk()
    else:
        root = ctk.CTk()
    
    app = UltimateFB2Translator(root)
    root.mainloop()