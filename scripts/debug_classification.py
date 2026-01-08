#!/usr/bin/env python3
"""Debug script to test classification on a specific PDF document."""

import json
import sys
from pathlib import Path

from documentor.config import load_config, get_openai_client
from documentor.models import DocumentMetadataRaw
from documentor.llm import get_system_prompt_raw_extraction, get_tools_raw_extraction
from documentor.pdf import render_pdf_to_images

# Load config
config = load_config()
OPENROUTER_MODEL_ID = config["OPENROUTER_MODEL_ID"]

print(f"Model: {OPENROUTER_MODEL_ID}")
print(f"Base URL: {config['OPENROUTER_BASE_URL']}")

client = get_openai_client()


def classify_document(pdf_path: Path):
    """Classify a PDF document and return full debug info."""
    print(f"\n{'='*60}")
    print(f"Classifying: {pdf_path}")
    print(f"{'='*60}")

    # Render images
    images_b64 = render_pdf_to_images(pdf_path)
    print(f"Rendered {len(images_b64)} page(s)")
    for i, img_b64 in enumerate(images_b64):
        print(f"Page {i+1}: {len(img_b64)} bytes base64")

    # Build user content
    user_content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
        }
        for img_b64 in images_b64
    ]

    system_prompt = get_system_prompt_raw_extraction()
    tools = get_tools_raw_extraction()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    print(f"\nSending request to OpenRouter...")
    print(f"System prompt length: {len(system_prompt)} chars")
    print(f"Tool schema: {json.dumps(tools[0]['function']['parameters'], indent=2)}")

    response = client.chat.completions.create(
        model=OPENROUTER_MODEL_ID,
        max_tokens=4096,
        temperature=0,
        messages=messages,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "extract_document_metadata"}},
    )

    print(f"\n{'='*60}")
    print("FULL RESPONSE:")
    print(f"{'='*60}")
    print(f"ID: {response.id}")
    print(f"Model: {response.model}")
    print(f"Created: {response.created}")

    choice = response.choices[0]
    print(f"\nFinish reason: {choice.finish_reason}")
    print(f"Message role: {choice.message.role}")
    print(f"Message content: {choice.message.content}")
    print(f"Tool calls: {choice.message.tool_calls}")

    if choice.message.tool_calls:
        for i, tc in enumerate(choice.message.tool_calls):
            print(f"\n--- Tool Call {i} ---")
            print(f"ID: {tc.id}")
            print(f"Type: {tc.type}")
            print(f"Function name: {tc.function.name}")
            print(f"Function arguments: {tc.function.arguments}")

            # Parse and pretty print the arguments
            try:
                args = json.loads(tc.function.arguments)
                print(f"\nParsed arguments:")
                print(json.dumps(args, indent=2))
            except json.JSONDecodeError as e:
                print(f"Failed to parse arguments: {e}")
    else:
        print("\nNO TOOL CALLS RETURNED!")
        print("The model did not call the extraction tool.")
        print("This is the error that caused the classification failure.")

    if response.usage:
        print(f"\nUsage:")
        print(f"  Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  Completion tokens: {response.usage.completion_tokens}")
        print(f"  Total tokens: {response.usage.total_tokens}")

    return response


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_classification.py <pdf_path>")
        sys.exit(1)
    else:
        pdf_path = Path(sys.argv[1])

    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)

    classify_document(pdf_path)
