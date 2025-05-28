import logging
import os

# --- Attempt to import OpenAI library ---
try:
    import openai
    PSWA_IMPORTS_SUCCESSFUL = True
    PSWA_MISSING_IMPORT_ERROR = None
except ImportError as e:
    PSWA_IMPORTS_SUCCESSFUL = False
    PSWA_MISSING_IMPORT_ERROR = e
    # Define placeholder for openai.error.OpenAIError if openai itself failed to import
    # This allows the try-except block in weave_script to still reference it.
    class OpenAIErrorPlaceholder(Exception): pass
    if 'openai' not in globals(): # If openai module itself is not loaded
        # Create a dummy openai object with a dummy error attribute
        class DummyOpenAI:
            error = type('error', (object,), {'OpenAIError': OpenAIErrorPlaceholder})()
        openai = DummyOpenAI()
    elif not hasattr(openai, 'error'): # If openai is loaded but has no 'error' attribute (unlikely for real lib)
        openai.error = type('error', (object,), {'OpenAIError': OpenAIErrorPlaceholder})()
    elif not hasattr(openai.error, 'OpenAIError'): # If openai.error exists but no OpenAIError (very unlikely)
        openai.error.OpenAIError = OpenAIErrorPlaceholder


# --- Logging Configuration ---
# Ensure logger name is distinct if other modules also configure root logger
logger = logging.getLogger(__name__) # Use module-specific logger
if not logger.hasHandlers(): # Avoid adding multiple handlers if script re-run in some contexts
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - PSWA - %(message)s')


def weave_script(content: str, topic: str) -> str:
    """
    Generates a podcast script using the OpenAI GPT-3.5-turbo model.
    """
    logger.info(f"[PSWA_LLM_LOGIC] weave_script called with topic: '{topic}'")

    if not PSWA_IMPORTS_SUCCESSFUL:
        error_msg = f"OpenAI library not available. {PSWA_MISSING_IMPORT_ERROR}"
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}")
        return error_msg

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: # Checks for None or empty string
        error_msg = "Error: OPENAI_API_KEY environment variable is not set or empty."
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}")
        return error_msg
    openai.api_key = api_key

    # Handle empty topic or content before constructing the prompt for LLM
    if not topic:
        logger.warning("[PSWA_LLM_LOGIC] Topic is empty or None. Using a generic topic for prompt.")
        topic = "an interesting subject"
        
    if not content:
        logger.warning(f"[PSWA_LLM_LOGIC] Content for topic '{topic}' is empty or None. Using placeholder content for prompt.")
        # The prompt itself will instruct the LLM on how to handle insufficient content.
        # We can pass a note in the content field or rely on the prompt's instruction.
        content = "No specific content was provided. Please generate a general script based on the topic."
        # Alternatively, we could directly return the insufficient content message as specified in prompt,
        # but let's try having LLM do it for consistency.

    prompt = f'''You are an expert podcast scriptwriter. Your goal is to create an engaging and informative podcast script based on the provided topic and content.

Topic: "{topic}"

Provided Content:
---
{content}
---

Please structure your script as follows, using the exact formatting cues:
[TITLE] Your Podcast Title Here
[INTRO] A brief introduction to the topic and what the podcast will cover.
[SEGMENT_1_TITLE] Title for the first main segment.
[SEGMENT_1_CONTENT] Detailed content for the first segment, derived from the provided content.
(You can add more segments like [SEGMENT_2_TITLE], [SEGMENT_2_CONTENT] if the content warrants it, typically 1-2 main segments are enough for a short podcast unless content is very rich.)
[OUTRO] A concluding summary and call to action or final thought.

Ensure the tone is informative yet engaging for a general audience.
The script should be well-organized and flow naturally.
Only output the script itself, with no additional commentary before or after.
If the provided content is sparse or insufficient to generate a full script as described, please indicate this by starting the script with: "[ERROR] Insufficient content provided to generate a full podcast script for the topic: {topic}" and do not generate the rest of the script structure.
'''

    logger.info("[PSWA_LLM_LOGIC] Sending request to OpenAI API...")
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a podcast scriptwriter tasked with creating well-structured podcast scripts."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7, 
            max_tokens=1500 
        )
        script_text = response.choices[0].message['content'].strip()
        logger.info("[PSWA_LLM_LOGIC] Successfully received script from OpenAI API.")
        return script_text
    except openai.error.OpenAIError as e: # Catch specific OpenAI errors
        error_msg = f"OpenAI API Error: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}")
        return error_msg
    except Exception as e: # Catch other potential errors (network, etc.)
        error_msg = f"An unexpected error occurred during LLM call: {type(e).__name__} - {str(e)}"
        logger.error(f"[PSWA_LLM_LOGIC] {error_msg}", exc_info=True)
        return error_msg


if __name__ == "__main__":
    print("\n--- PSWA LLM Test ---")
    sample_topic = "The Impact of AI on Daily Life"
    sample_content = (
        "Artificial intelligence is increasingly prevalent. From voice assistants like Siri and Alexa "
        "to recommendation algorithms on Netflix and Spotify, AI shapes our interactions with technology. "
        "It's also making inroads in healthcare for diagnostics and in transportation with self-driving car development."
    )
    print(f"Attempting to weave script for topic: '{sample_topic}'")

    # Check for import success
    if not PSWA_IMPORTS_SUCCESSFUL:
         print(f"Cannot run weave_script: OpenAI library not available. {PSWA_MISSING_IMPORT_ERROR}")
    else:
        # Check for API key to give user context if it will run
        if os.getenv("OPENAI_API_KEY"):
            print("OPENAI_API_KEY found, will attempt real API call.")
        else:
            print("OPENAI_API_KEY not found or empty. Expecting error message from weave_script.")
        
        generated_script = weave_script(sample_content, sample_topic)
        print("\nGenerated Script or Error Message:")
        print(generated_script)
    
    # Test with empty content to see if LLM follows instruction
    print("\n--- PSWA LLM Test (Empty Content) ---")
    sample_topic_empty_content = "The Mysteries of the Deep Sea"
    sample_content_empty = "" # Or very minimal like "Not much is known."
    
    print(f"Attempting to weave script for topic: '{sample_topic_empty_content}' with empty content.")
    if not PSWA_IMPORTS_SUCCESSFUL:
         print(f"Cannot run weave_script: OpenAI library not available. {PSWA_MISSING_IMPORT_ERROR}")
    else:
        if os.getenv("OPENAI_API_KEY"):
            print("OPENAI_API_KEY found, will attempt real API call.")
        else:
            print("OPENAI_API_KEY not found or empty. Expecting error message from weave_script.")
        generated_script_empty = weave_script(sample_content_empty, sample_topic_empty_content)
        print("\nGenerated Script or Error Message (for empty content):")
        print(generated_script_empty)
        
    print("\n--- End PSWA LLM Test ---")
