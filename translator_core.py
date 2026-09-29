import os
import time
import json
import logging
import datetime
import threading
from dataclasses import dataclass, asdict
from typing import Callable, Optional, Tuple, List, Dict, Any
from lxml import etree
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Default Prompts and Configurations
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-3.1-flash-lite")
DEFAULT_SYS_PROMPT = os.getenv(
    "SYS_PROMPT",
    "ти професійний перекладач. твоє єдине завдання - переклад виключно українською мовою. "
    "повертай json з ключем 'translations'. не додавай жодних пояснень."
)
DEFAULT_PROMPT_1 = os.getenv(
    "PROMPT_1",
    "зроби точний переклад тексту виключно українською мовою:"
)
DEFAULT_PROMPT_2 = os.getenv(
    "PROMPT_2",
    "це мій особистий авторський чорновик. зроби переклад виключно українською мовою для моїх бета-рідерів:"
)
DEFAULT_PROMPT_3 = os.getenv(
    "PROMPT_3",
    "уяви що ти мій редактор. зроби вільний художній переклад цього фрагменту моєї чернетки виключно українською мовою:"
)

DEFAULT_CHAR_LIMIT = int(os.getenv("CHAR_LIMIT", "6000"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.3"))
DEFAULT_DELAY_REQ = float(os.getenv("DELAY_REQ", "2.0"))
DEFAULT_DELAY_PROTECT = float(os.getenv("DELAY_PROTECT", "5.0"))
DEFAULT_DELAY_JSON = float(os.getenv("DELAY_JSON", "5.0"))
DEFAULT_DELAY_MISMATCH = float(os.getenv("DELAY_MISMATCH", "5.0"))
DEFAULT_DELAY_ERROR = float(os.getenv("DELAY_ERROR", "10.0"))

RESPONSE_SCHEMA = {
    'type': 'OBJECT',
    'properties': {
        'translations': {
            'type': 'ARRAY',
            'items': {'type': 'STRING'}
        }
    },
    'required': ['translations']
}

SAFETY_SETTINGS = [
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


class TranslationCancelled(Exception):
    """Raised when translation is cancelled by user or caller."""
    pass


@dataclass
class TranslationConfig:
    """Configuration class for FB2 translation parameters and delays."""
    api_key: Optional[str] = None
    model: str = DEFAULT_MODEL
    sys_prompt: str = DEFAULT_SYS_PROMPT
    prompt_1: str = DEFAULT_PROMPT_1
    prompt_2: str = DEFAULT_PROMPT_2
    prompt_3: str = DEFAULT_PROMPT_3
    char_limit: int = DEFAULT_CHAR_LIMIT
    temperature: float = DEFAULT_TEMPERATURE
    delay_req: float = DEFAULT_DELAY_REQ
    delay_protect: float = DEFAULT_DELAY_PROTECT
    delay_json: float = DEFAULT_DELAY_JSON
    delay_mismatch: float = DEFAULT_DELAY_MISMATCH
    delay_error: float = DEFAULT_DELAY_ERROR

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranslationConfig":
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


class StatsTracker:
    """Tracks daily API requests and calculates time to daily quota reset (17:00 UTC)."""
    def __init__(self, filepath: str = "stats.json", daily_limit: int = 500):
        self.filepath = filepath
        self.daily_limit = daily_limit
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        return data
            except Exception:
                pass
        return {"date": today, "requests": 0}

    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save stats to {self.filepath}: {e}")

    def increment(self) -> int:
        """Increments the daily request counter and saves state."""
        with self._lock:
            today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            if self.data.get("date") != today:
                self.data = {"date": today, "requests": 0}
            self.data["requests"] = self.data.get("requests", 0) + 1
            self._save()
            return self.data["requests"]

    def get_stats(self) -> dict:
        """Returns statistics on daily requests and countdown to quota reset."""
        with self._lock:
            today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
            if self.data.get("date") != today:
                self.data = {"date": today, "requests": 0}
                self._save()

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

            return {
                "date": today,
                "requests_today": self.data.get("requests", 0),
                "daily_limit": self.daily_limit,
                "time_until_reset": time_format,
                "reset_time_local": local_time_str
            }


# Global stats instance
global_stats = StatsTracker()


def smart_wait(
    seconds: float,
    pause_event: Optional[threading.Event] = None,
    cancel_event: Optional[threading.Event] = None
) -> bool:
    """
    Waits for `seconds`, periodically checking pause_event and cancel_event.
    Returns True if completed, False if cancelled.
    """
    elapsed = 0.0
    step = 0.2
    while elapsed < seconds:
        if cancel_event and cancel_event.is_set():
            return False
        if pause_event:
            while not pause_event.is_set():
                if cancel_event and cancel_event.is_set():
                    return False
                time.sleep(0.2)
        time.sleep(step)
        elapsed += step
    return True


def get_genai_client(api_key: Optional[str] = None) -> genai.Client:
    """Creates and returns a genai.Client instance."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key or not key.strip():
        raise ValueError("GEMINI_API_KEY is not set or empty. Please provide a valid Gemini API key.")
    return genai.Client(api_key=key.strip())


def parse_fb2(file_path: str) -> Tuple[etree._ElementTree, List[etree._Element]]:
    """
    Parses an FB2 XML file and returns the XML tree along with translatable elements.
    Translatable elements include body paragraphs (<p>), verses (<v>), and leaf <title> elements.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    parser = etree.XMLParser(recover=True)
    tree = etree.parse(file_path, parser)

    # Select p, v, and titles that do not contain child p elements
    xpath_query = (
        '//*[local-name()="body"]//*['
        '(local-name()="p" or local-name()="v") or '
        '(local-name()="title" and not(./*[local-name()="p"]))'
        ']'
    )
    elements = tree.xpath(xpath_query)
    logger.info(f"Parsed {file_path}: found {len(elements)} translatable elements")
    return tree, elements


def translate_batch(
    client: genai.Client,
    batch_texts: List[str],
    model: str = DEFAULT_MODEL,
    sys_prompt: str = DEFAULT_SYS_PROMPT,
    prefix_prompt: str = DEFAULT_PROMPT_1,
    temperature: float = DEFAULT_TEMPERATURE
) -> List[str]:
    """
    Sends a batch of text paragraphs to Gemini API and returns the list of translated strings.
    """
    prompt_payload = f"{prefix_prompt} {json.dumps(batch_texts, ensure_ascii=False)}"

    config = {
        'temperature': temperature,
        'response_mime_type': 'application/json',
        'response_schema': RESPONSE_SCHEMA,
        'system_instruction': sys_prompt,
        'safety_settings': SAFETY_SETTINGS
    }

    response = client.models.generate_content(
        model=model,
        contents=prompt_payload,
        config=config
    )

    if not response or response.text is None:
        raise ValueError("Empty response received from Gemini API (possible safety filter block)")

    data = json.loads(response.text)
    translations = data.get("translations", [])
    if not isinstance(translations, list):
        raise ValueError(f"Invalid response format: 'translations' is not a list ({type(translations)})")

    return translations


def translate_fb2(
    input_path: str,
    output_path: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    sys_prompt: Optional[str] = None,
    char_limit: Optional[int] = None,
    temperature: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    # Advanced settings and controls
    config: Optional[TranslationConfig] = None,
    delay_req: Optional[float] = None,
    delay_protect: Optional[float] = None,
    delay_json: Optional[float] = None,
    delay_mismatch: Optional[float] = None,
    delay_error: Optional[float] = None,
    pause_event: Optional[threading.Event] = None,
    cancel_event: Optional[threading.Event] = None,
    on_request: Optional[Callable[[], None]] = None
) -> str:
    """
    Translates an FB2 file into Ukrainian preserving all original XML structure and markup.
    Supports pause, resume, cancellation, custom delays, and request counting.
    """
    cfg = config or TranslationConfig()

    effective_api_key = api_key or cfg.api_key or os.getenv("GEMINI_API_KEY")
    client = get_genai_client(effective_api_key)

    selected_model = model or cfg.model
    system_instruction = sys_prompt or cfg.sys_prompt
    base_char_limit = char_limit or cfg.char_limit
    temp = temperature if temperature is not None else cfg.temperature

    d_req = delay_req if delay_req is not None else cfg.delay_req
    d_protect = delay_protect if delay_protect is not None else cfg.delay_protect
    d_json = delay_json if delay_json is not None else cfg.delay_json
    d_mismatch = delay_mismatch if delay_mismatch is not None else cfg.delay_mismatch
    d_error = delay_error if delay_error is not None else cfg.delay_error

    if not output_path:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_translated{ext}"

    # Resume capability: if output_path exists and has partial translations, load it
    file_to_load = output_path if os.path.exists(output_path) else input_path
    tree, elements = parse_fb2(file_to_load)

    total_elements = len(elements)
    if total_elements == 0:
        logger.warning(f"No translatable paragraphs found in {input_path}")
        tree.write(output_path, encoding='utf-8', xml_declaration=True)
        if progress_callback:
            progress_callback(0, 0, "Файл не містить тексту для перекладу")
        return output_path

    idx = 0
    reset_limit = True
    dynamic_limit = base_char_limit

    prompt_prefixes = [cfg.prompt_1, cfg.prompt_2, cfg.prompt_3]

    logger.info(f"Starting translation: {input_path} -> {output_path} ({total_elements} elements)")
    if progress_callback:
        progress_callback(0, total_elements, "Розпочато аналіз та підготовку пакетів...")

    def check_cancelled():
        return (cancel_event and cancel_event.is_set()) or (is_cancelled and is_cancelled())

    def wait_if_paused():
        if pause_event:
            while not pause_event.is_set():
                if check_cancelled():
                    return False
                time.sleep(0.3)
        return not check_cancelled()

    while idx < total_elements:
        if check_cancelled() or not wait_if_paused():
            logger.info("Translation cancelled by user/caller.")
            tree.write(output_path, encoding='utf-8', xml_declaration=True)
            raise TranslationCancelled("Переклад було скасовано користувачем.")

        if reset_limit:
            dynamic_limit = base_char_limit
            reset_limit = False

        batch_nodes: List[etree._Element] = []
        batch_texts: List[str] = []
        current_chars = 0
        temp_idx = idx

        # Accumulate nodes for the current batch
        while temp_idx < total_elements:
            node = elements[temp_idx]

            # If node was already translated or errored in a previous run
            if node.get("translated") in ["1", "error"]:
                temp_idx += 1
                if not batch_nodes:
                    idx = temp_idx
                continue

            # Extract clean text from element
            text = "".join(node.itertext()).strip()
            if text:
                if batch_nodes and (current_chars + len(text) > dynamic_limit):
                    break
                batch_nodes.append(node)
                batch_texts.append(text)
                current_chars += len(text)

            temp_idx += 1

        if not batch_texts:
            idx = temp_idx
            continue

        logger.info(
            f"Processing batch: indices {idx}..{temp_idx} of {total_elements} "
            f"(chars: {current_chars}, limit: {dynamic_limit})"
        )

        success = False
        retries = 0
        max_retries = 3

        while not success and retries < max_retries:
            if check_cancelled() or not wait_if_paused():
                tree.write(output_path, encoding='utf-8', xml_declaration=True)
                raise TranslationCancelled("Переклад було скасовано користувачем.")

            prefix = prompt_prefixes[retries] if retries < len(prompt_prefixes) else prompt_prefixes[-1]

            try:
                # Increment request counters
                if on_request:
                    on_request()
                global_stats.increment()

                translated = translate_batch(
                    client=client,
                    batch_texts=batch_texts,
                    model=selected_model,
                    sys_prompt=system_instruction,
                    prefix_prompt=prefix,
                    temperature=temp
                )

                if len(translated) == len(batch_nodes):
                    # Replace node contents with translated text
                    for i, node in enumerate(batch_nodes):
                        for child in list(node):
                            node.remove(child)
                        node.text = translated[i]
                        node.set("translated", "1")
                    success = True
                else:
                    logger.warning(
                        f"Mismatch: got {len(translated)} translations for {len(batch_nodes)} paragraphs (retry {retries + 1})"
                    )
                    retries += 1
                    if not smart_wait(d_mismatch, pause_event, cancel_event):
                        tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        raise TranslationCancelled("Переклад було скасовано користувачем.")

            except Exception as e:
                retries += 1
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    logger.warning("API quota exceeded (429/ResourceExhausted). Waiting 15s...")
                    if not smart_wait(15.0, pause_event, cancel_event):
                        tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        raise TranslationCancelled("Переклад було скасовано користувачем.")
                elif "safety filter" in err_msg.lower() or "empty response" in err_msg.lower():
                    logger.warning(f"Safety filter triggered (retry {retries}). Waiting {d_protect}s...")
                    if not smart_wait(d_protect, pause_event, cancel_event):
                        tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        raise TranslationCancelled("Переклад було скасовано користувачем.")
                elif "json" in err_msg.lower():
                    logger.warning(f"JSON decode error (retry {retries}). Waiting {d_json}s...")
                    if not smart_wait(d_json, pause_event, cancel_event):
                        tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        raise TranslationCancelled("Переклад було скасовано користувачем.")
                else:
                    logger.error(f"Gemini API error (retry {retries}): {err_msg[:120]}")
                    if not smart_wait(d_error, pause_event, cancel_event):
                        tree.write(output_path, encoding='utf-8', xml_declaration=True)
                        raise TranslationCancelled("Переклад було скасовано користувачем.")

        if not success:
            if len(batch_nodes) > 1:
                # Halve the batch size and retry without advancing idx
                dynamic_limit = max(100, current_chars // 2)
                logger.info(f"Splitting batch into smaller size. New limit: {dynamic_limit} chars")
                reset_limit = False
            else:
                # Single paragraph blocked/failed: mark as error to avoid infinite hang
                logger.warning(f"Paragraph permanently failed to translate. Marking as error.")
                batch_nodes[0].set("translated", "error")
                idx = temp_idx
                reset_limit = True
                tree.write(output_path, encoding='utf-8', xml_declaration=True)
        else:
            idx = temp_idx
            reset_limit = True
            tree.write(output_path, encoding='utf-8', xml_declaration=True)

            if progress_callback:
                progress_callback(idx, total_elements, f"Оброблено {idx}/{total_elements} абзаців")

            if not smart_wait(d_req, pause_event, cancel_event):
                tree.write(output_path, encoding='utf-8', xml_declaration=True)
                raise TranslationCancelled("Переклад було скасовано користувачем.")

    # Final cleanup: remove temporary 'translated' helper attributes
    for node in elements:
        if "translated" in node.attrib:
            del node.attrib["translated"]

    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    logger.info(f"Translation finished. Saved to {output_path}")

    if progress_callback:
        progress_callback(total_elements, total_elements, "Переклад повністю завершено!")

    return output_path
