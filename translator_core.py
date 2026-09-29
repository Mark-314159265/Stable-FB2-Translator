import os
import time
import json
import logging
from typing import Callable, Optional, Tuple, List
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
    is_cancelled: Optional[Callable[[], bool]] = None
) -> str:
    """
    Translates an FB2 file into Ukrainian preserving all original XML structure and markup.

    Args:
        input_path: Path to the source FB2 file.
        output_path: Target path for the translated FB2. If None, appends '_translated.fb2'.
        api_key: Google Gemini API key. If None, reads from GEMINI_API_KEY environment variable.
        model: Gemini model name (default: GEMINI_MODEL env or models/gemini-3.1-flash-lite).
        sys_prompt: System prompt for Gemini.
        char_limit: Max characters per translation batch.
        temperature: Model sampling temperature.
        progress_callback: Optional callback func(current_idx, total_count, status_message).
        is_cancelled: Optional callback returning True if processing should be cancelled.

    Returns:
        The path to the generated output file.
    """
    client = get_genai_client(api_key)
    selected_model = model or DEFAULT_MODEL
    system_instruction = sys_prompt or DEFAULT_SYS_PROMPT
    base_char_limit = char_limit or DEFAULT_CHAR_LIMIT
    temp = temperature if temperature is not None else DEFAULT_TEMPERATURE

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

    prompt_prefixes = [DEFAULT_PROMPT_1, DEFAULT_PROMPT_2, DEFAULT_PROMPT_3]

    logger.info(f"Starting translation: {input_path} -> {output_path} ({total_elements} elements)")
    if progress_callback:
        progress_callback(0, total_elements, "Розпочато аналіз та підготовку пакетів...")

    while idx < total_elements:
        if is_cancelled and is_cancelled():
            logger.info("Translation cancelled by user/caller.")
            break

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

        logger.info(f"Processing batch: indices {idx}..{temp_idx} of {total_elements} (chars: {current_chars}, limit: {dynamic_limit})")

        success = False
        retries = 0
        max_retries = 3

        while not success and retries < max_retries:
            if is_cancelled and is_cancelled():
                break

            prefix = prompt_prefixes[retries] if retries < len(prompt_prefixes) else DEFAULT_PROMPT_3

            try:
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
                    time.sleep(DEFAULT_DELAY_MISMATCH)
                    retries += 1

            except Exception as e:
                retries += 1
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    logger.warning("API quota exceeded (429/ResourceExhausted). Waiting 15s...")
                    time.sleep(15.0)
                elif "safety filter" in err_msg.lower() or "empty response" in err_msg.lower():
                    logger.warning(f"Safety filter triggered (retry {retries}). Waiting {DEFAULT_DELAY_PROTECT}s...")
                    time.sleep(DEFAULT_DELAY_PROTECT)
                elif "json" in err_msg.lower():
                    logger.warning(f"JSON decode error (retry {retries}). Waiting {DEFAULT_DELAY_JSON}s...")
                    time.sleep(DEFAULT_DELAY_JSON)
                else:
                    logger.error(f"Gemini API error (retry {retries}): {err_msg[:120]}")
                    time.sleep(DEFAULT_DELAY_ERROR)

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

            time.sleep(DEFAULT_DELAY_REQ)

    # Final cleanup: remove temporary 'translated' helper attributes
    for node in elements:
        if "translated" in node.attrib:
            del node.attrib["translated"]

    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    logger.info(f"Translation finished. Saved to {output_path}")

    if progress_callback:
        progress_callback(total_elements, total_elements, "Переклад повністю завершено!")

    return output_path
