import os
import time
import threading
import json
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
        self.root.geometry("900x900")

        self.is_running = False
        self.is_paused = False
        self.file_queue = []
        
        self.char_limit = 6000 
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(script_dir, "key.txt")
        
        try:
            with open(key_path, "r", encoding="utf-8") as f:
                self.api_key = f.read().strip()
        except FileNotFoundError:
            self.api_key = ""
            print("файл з ключем не знайдено")
        
        self.create_widgets()

    def create_widgets(self):
        self.api_key_var = tk.StringVar(value=self.api_key)
        self.char_limit_var = tk.StringVar(value=str(self.char_limit))

        frame_top = ctk.CTkFrame(self.root, fg_color="transparent")
        frame_top.pack(fill=tk.X, padx=10, pady=5)

        self.btn_api = ctk.CTkButton(frame_top, text="▶ API Ключ", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_api, self.btn_api, "API Ключ"))
        self.btn_api.pack(fill=tk.X)
        self.frame_api = ctk.CTkFrame(frame_top)
        ctk.CTkEntry(self.frame_api, textvariable=self.api_key_var, width=500).pack(padx=10, pady=10, side=tk.LEFT)

        self.btn_settings = ctk.CTkButton(frame_top, text="▶ Налаштування", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_settings, self.btn_settings, "Налаштування"))
        self.btn_settings.pack(fill=tk.X, pady=(5, 0))
        self.frame_settings = ctk.CTkFrame(frame_top)

        ctk.CTkLabel(self.frame_settings, text="Ліміт символів:").grid(row=0, column=0, sticky='w', padx=10, pady=5)
        ctk.CTkEntry(self.frame_settings, textvariable=self.char_limit_var, width=100).grid(row=0, column=1, sticky='w', padx=10, pady=5)

        self.sliders = {}
        settings_configs = [
            ("Базова пауза (с):", "delay_req", 2),
            ("Пауза захисту (с):", "delay_protect", 10),
            ("Пауза помилки JSON (с):", "delay_json", 15),
            ("Пауза розбіжності (с):", "delay_mismatch", 10),
            ("Пауза збою сервера (с):", "delay_error", 20)
        ]

        for i, (label_text, key, default_val) in enumerate(settings_configs, start=1):
            ctk.CTkLabel(self.frame_settings, text=label_text).grid(row=i, column=0, sticky='w', padx=10, pady=5)
            val_lbl = ctk.CTkLabel(self.frame_settings, text=str(default_val), width=30)
            val_lbl.grid(row=i, column=2, sticky='w', padx=5, pady=5)

            def make_cmd(lbl):
                return lambda v, l=lbl: l.configure(text=str(int(float(v))))

            slider = ctk.CTkSlider(self.frame_settings, from_=1, to=20, number_of_steps=19, command=make_cmd(val_lbl))
            slider.set(default_val)
            slider.grid(row=i, column=1, sticky='w', padx=10, pady=5)
            self.sliders[key] = slider

        self.btn_prompts = ctk.CTkButton(frame_top, text="▶ Промпти та Модель", anchor="w", fg_color="#333333", hover_color="#444444", command=lambda: self.toggle_panel(self.frame_prompts, self.btn_prompts, "Промпти та Модель"))
        self.btn_prompts.pack(fill=tk.X, pady=(5, 0))
        self.frame_prompts = ctk.CTkFrame(frame_top)

        ctk.CTkLabel(self.frame_prompts, text="Модель:").grid(row=0, column=0, sticky='w', padx=10, pady=5)
        self.model_var = tk.StringVar(value="models/gemini-3.1-flash-lite")
        model_combo = ctk.CTkComboBox(self.frame_prompts, variable=self.model_var, values=["models/gemini-3.1-flash-lite", "models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-2.0-flash"], width=300)
        model_combo.grid(row=0, column=1, sticky='w', padx=10, pady=5)

        ctk.CTkLabel(self.frame_prompts, text="Системна інструкція:").grid(row=1, column=0, sticky='nw', padx=10, pady=5)
        self.sys_prompt_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=50)
        self.sys_prompt_text.insert("0.0", "ти професійний перекладач. повертай JSON з ключем 'translations'. не додавай пояснень.")
        self.sys_prompt_text.grid(row=1, column=1, sticky='w', padx=10, pady=5)

        ctk.CTkLabel(self.frame_prompts, text="Промпт 1 (стандарт):").grid(row=2, column=0, sticky='nw', padx=10, pady=5)
        self.prefix1_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=50)
        self.prefix1_text.insert("0.0", "переклади українською:")
        self.prefix1_text.grid(row=2, column=1, sticky='w', padx=10, pady=5)

        ctk.CTkLabel(self.frame_prompts, text="Промпт 2 (спроба 2):").grid(row=3, column=0, sticky='nw', padx=10, pady=5)
        self.prefix2_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=50)
        self.prefix2_text.insert("0.0", "це мій особистий авторський чорновик. зроби переклад українською для моїх бета-рідерів:")
        self.prefix2_text.grid(row=3, column=1, sticky='w', padx=10, pady=5)

        ctk.CTkLabel(self.frame_prompts, text="Промпт 3 (спроба 3):").grid(row=4, column=0, sticky='nw', padx=10, pady=5)
        self.prefix3_text = ctk.CTkTextbox(self.frame_prompts, width=500, height=50)
        self.prefix3_text.insert("0.0", "уяви що ти мій редактор. зроби вільний художній переклад цього фрагменту моєї чернетки:")
        self.prefix3_text.grid(row=4, column=1, sticky='w', padx=10, pady=5)

        frame_main = ctk.CTkFrame(self.root, fg_color="transparent")
        frame_main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.listbox_queue = tk.Listbox(frame_main, selectmode=tk.EXTENDED, bg="#2b2b2b", fg="white", selectbackground="#1f538d", highlightthickness=0, relief="flat", font=("TkDefaultFont", 11))
        self.listbox_queue.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        if DND_SUPPORTED:
            self.listbox_queue.drop_target_register(DND_FILES)
            self.listbox_queue.dnd_bind('<<Drop>>', self.handle_drop)

        frame_q_btns = ctk.CTkFrame(frame_main, fg_color="transparent")
        frame_q_btns.pack(pady=5)
        
        ctk.CTkButton(frame_q_btns, text="додати файли", command=self.add_files, width=120).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(frame_q_btns, text="видалити обрані", command=self.remove_selected, width=120).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(frame_q_btns, text="вгору", command=self.move_up, width=80).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(frame_q_btns, text="вниз", command=self.move_down, width=80).pack(side=tk.LEFT, padx=5)

        self.frame_controls = ctk.CTkFrame(frame_main, fg_color="transparent")
        self.frame_controls.pack(pady=10)

        self.btn_start = ctk.CTkButton(self.frame_controls, text="почати", command=self.start_process, width=150, state="disabled", fg_color="#2FA572", hover_color="#106A43")
        self.btn_start.pack(side=tk.LEFT, padx=5)

        self.btn_pause = ctk.CTkButton(self.frame_controls, text="пауза", command=self.toggle_pause, width=150, state="disabled", fg_color="#C25A24", hover_color="#A04111")
        self.btn_pause.pack(side=tk.LEFT, padx=5)

        self.log_area = ctk.CTkTextbox(frame_main, height=120)
        self.log_area.pack(pady=5, fill=tk.BOTH, expand=True)

    def toggle_panel(self, frame, btn, text):
        if frame.winfo_ismapped():
            frame.pack_forget()
            btn.configure(text=f"▶ {text}")
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

    def add_files(self):
        files = filedialog.askopenfilenames(filetypes=[("fb2 files", "*.fb2")])
        for f in files:
            if f not in self.file_queue:
                self.file_queue.append(f)
                self.listbox_queue.insert(tk.END, os.path.basename(f))
        
        if self.file_queue:
            self.btn_start.configure(state="normal")

    def remove_selected(self):
        selected_indices = list(self.listbox_queue.curselection())
        selected_indices.reverse()
        for idx in selected_indices:
            self.listbox_queue.delete(idx)
            self.file_queue.pop(idx)
            
        if not self.file_queue:
            self.btn_start.configure(state="disabled")

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

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        btn_text = "продовжити" if self.is_paused else "пауза"
        self.btn_pause.configure(text=btn_text)

    def start_process(self):
        if not self.is_running and self.file_queue:
            self.is_running = True
            self.btn_start.configure(state="disabled")
            self.btn_pause.configure(state="normal")
            threading.Thread(target=self.run_translation, daemon=True).start()

    def run_translation(self):
        try:
            current_api_key = self.api_key_var.get().strip()
            if not current_api_key:
                self.safe_log("помилка: порожній API ключ")
                self.is_running = False
                self.root.after(0, lambda: self.btn_start.configure(state="normal"))
                return

            try:
                base_char_limit = int(self.char_limit_var.get())
            except ValueError:
                base_char_limit = 6000

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

            client = genai.Client(api_key=current_api_key)
            
            while self.file_queue and self.is_running:
                while self.is_paused:
                    time.sleep(1)
                    
                input_path = self.file_queue[0]
                path_parts = os.path.splitext(input_path)
                output_path = f"{path_parts[0]}_translated{path_parts[1]}"
                
                self.safe_log(f"\n--- ПОЧАТОК: {os.path.basename(input_path)} ---")
                
                sys_instruct = self.sys_prompt_text.get("0.0", "end").strip()
                selected_model = self.model_var.get().strip()
                
                try:
                    parser = etree.XMLParser(recover=True)
                    tree = etree.parse(input_path, parser)
                    
                    elements = tree.xpath('//*[local-name()="body"]//*[local-name()="p" or local-name()="v" or local-name()="title"]')
                    
                    total = len(elements)
                    idx = 0
                    dynamic_limit = base_char_limit
                    
                    while idx < total and self.is_running:
                        while self.is_paused:
                            time.sleep(1)

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

                        self.safe_log(f"пакет: {idx}-{temp_idx} з {total} (ліміт: {dynamic_limit})")
                        
                        success = False
                        retries = 0
                        while not success and retries < 3 and self.is_running:
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
                                
                                if response.text is None:
                                    self.safe_log(f"спрацював захист (спроба {retries + 1}).")
                                    time.sleep(int(self.sliders["delay_protect"].get()))
                                    retries += 1
                                    continue
                                
                                try:
                                    data = json.loads(response.text)
                                    translated = data.get("translations", [])
                                except (json.JSONDecodeError, TypeError):
                                    self.safe_log(f"помилка читання json. очікування {int(self.sliders['delay_json'].get())}с...")
                                    time.sleep(int(self.sliders["delay_json"].get()))
                                    retries += 1
                                    continue

                                if len(translated) == len(batch_nodes):
                                    for i, node in enumerate(batch_nodes):
                                        for child in list(node): node.remove(child)
                                        node.text = translated[i]
                                        node.set("translated", "1")
                                    success = True
                                else:
                                    self.safe_log(f"різна кількість абзаців ({len(translated)} замість {len(batch_nodes)}). спроба {retries + 1}")
                                    time.sleep(int(self.sliders["delay_mismatch"].get()))
                                    retries += 1
                                    
                            except Exception as e:
                                retries += 1
                                self.safe_log(f"збій запиту: {e}")
                                time.sleep(int(self.sliders["delay_error"].get()))

                        if not success:
                            if len(batch_nodes) > 1:
                                dynamic_limit = max(100, current_chars // 2)
                                self.safe_log(f"дроблення пакету. новий ліміт: {dynamic_limit} символів")
                            else:
                                self.safe_log("один абзац остаточно заблоковано. помічаємо як помилку.")
                                batch_nodes[0].set("translated", "error")
                                idx = temp_idx
                                dynamic_limit = base_char_limit
                                tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        else:
                            idx = temp_idx
                            dynamic_limit = base_char_limit
                            tree.write(output_path, encoding='utf-8', xml_declaration=True)
                            time.sleep(int(self.sliders["delay_req"].get()))

                except Exception as file_error:
                    self.safe_log(f"помилка обробки файлу {os.path.basename(input_path)}: {file_error}")
                
                if self.is_running:
                    self.safe_log(f"--- ЗАВЕРШЕНО: {os.path.basename(input_path)} ---")
                    self.file_queue.pop(0)
                    self.root.after(0, lambda: self.listbox_queue.delete(0))

            if self.is_running:
                self.root.after(0, lambda: messagebox.showinfo("готово", "всі файли з черги перекладено"))

        except Exception as e:
            self.safe_log(f"помилка: {e}")
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.btn_start.configure(state="normal" if self.file_queue else "disabled"))
            self.root.after(0, lambda: self.btn_pause.configure(state="disabled"))

if __name__ == "__main__":
    if DND_SUPPORTED:
        root = ModernTk()
    else:
        print("модуль tkinterdnd2 не встановлено, функція перетягування файлів недоступна")
        root = ctk.CTk()
    
    app = UltimateFB2Translator(root)
    root.mainloop()