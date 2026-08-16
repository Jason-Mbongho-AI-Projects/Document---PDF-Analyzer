# Import the PyPDF2 library to read and extract text and metadata from PDF files
import PyPDF2

# Import regular expressions for text pattern matching and cleaning
import re

# Import typing hints to specify expected types for function arguments and return values
from typing import List, Dict

# Import the tiktoken library for counting tokens based on a specific tokenizer model (useful for AI models like GPT)
import tiktoken


class PDFProcessor:
    def __init__(self):
        """
        Initialize the PDFProcessor class.
        Sets up the tokenizer encoding using tiktoken.
        This encoding helps determine how many tokens a text contains,
        which is important when working with models like OpenAI GPT.
        """
        self.encoding = tiktoken.get_encoding("cl100k_base")

    def extract_text_from_pdf(self, pdf_file) -> str:
        """
        Extracts all the text content from a PDF file.

        Args:
            pdf_file: The uploaded PDF file object.

        Returns:
            A single string containing the text from all pages of the PDF.
        """
        try:
            # Read the PDF file
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            text = ""

            # Loop through each page to extract text
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    # Extract text from the current page
                    page_text = page.extract_text()
                    # Append the extracted text with a page marker
                    text += f"\n--- Page {page_num + 1} ---\n{page_text}"
                except Exception as e:
                    # If a page fails to extract, log and skip it
                    print(f"Error extracting page {page_num + 1}: {e}")
                    continue

            return text
        except Exception as e:
            # Raise an error if the PDF could not be read at all
            raise Exception(f"Error reading PDF: {str(e)}")

    def clean_text(self, text: str) -> str:
        """
        Cleans and normalizes the extracted text.

        Steps:
        - Removes excessive whitespace
        - Strips headers like "--- Page X ---"
        - Removes special characters (retains useful punctuation)

        Args:
            text: The raw extracted text.

        Returns:
            A cleaned string with normalized formatting.
        """
        # Replace multiple newlines with a single newline
        text = re.sub(r'\n+', '\n', text)

        # Replace multiple spaces with a single space
        text = re.sub(r' +', ' ', text)

        # Remove page markers like "--- Page 1 ---"
        text = re.sub(r'--- Page \d+ ---', '', text)

        # Remove special characters except common punctuation
        text = re.sub(r'[^\w\s\.\,\!\?\;\:\-\(\)]', ' ', text)

        # Collapse any additional spaces
        text = ' '.join(text.split())

        return text.strip()

    def clean_pages(self, raw_text: str) -> List[Dict]:
        """
        Clean the extracted text one page at a time so provenance survives.

        clean_text strips the "--- Page N ---" headers that
        extract_text_from_pdf just inserted, which throws away the only link
        between a passage and the page it came from. This splits on those
        headers first and scrubs each page body separately, so nothing has to
        survive the special-character pass.

        Args:
            raw_text: Output of extract_text_from_pdf.

        Returns:
            A list of {'page': int, 'text': str} for pages with content.
        """
        parts = re.split(r'--- Page (\d+) ---', raw_text)

        pages: List[Dict] = []

        # re.split with one capture group yields [pre, page_no, body, page_no, body, ...].
        # Anything before the first marker belongs to page 1.
        preamble = self.clean_text(parts[0])
        if preamble:
            pages.append({'page': 1, 'text': preamble})

        for i in range(1, len(parts) - 1, 2):
            body = self.clean_text(parts[i + 1])
            if body:
                pages.append({'page': int(parts[i]), 'text': body})

        return pages

    def chunk_pages(self, pages: List[Dict], max_tokens: int = 8000) -> List[Dict]:
        """
        Chunk cleaned pages while tracking which pages each chunk spans.

        Unlike chunk_text, each sentence is encoded once and the counts are
        summed, rather than re-encoding the whole accumulated chunk on every
        sentence, which is quadratic in the length of a chunk.

        Args:
            pages: Output of clean_pages.
            max_tokens: The maximum number of tokens allowed per chunk.

        Returns:
            A list of {'text': str, 'first_page': int, 'last_page': int}.
        """
        # Flatten into (sentence, page, token_count) triples. split('. ') drops
        # the separator between sentences but leaves the terminator on the last
        # one of each page, so strip it here and re-add a single '.' on join —
        # otherwise every page boundary produces a doubled period.
        sentences: List[tuple] = []
        for page in pages:
            for sentence in page['text'].split('. '):
                sentence = sentence.strip()
                if sentence.endswith('.'):
                    sentence = sentence[:-1].rstrip()
                if sentence:
                    # +1 approximates the ". " re-joined below.
                    sentences.append((sentence, page['page'], self.count_tokens(sentence) + 1))

        def flush(buf: List[str], first: int, last: int) -> Dict:
            return {'text': '. '.join(buf) + '.', 'first_page': first, 'last_page': last}

        chunks: List[Dict] = []
        buffer: List[str] = []
        buffer_tokens = 0
        first_page = last_page = None

        for sentence, page, tokens in sentences:
            if buffer and buffer_tokens + tokens > max_tokens:
                chunks.append(flush(buffer, first_page, last_page))
                buffer, buffer_tokens, first_page = [], 0, None

            if first_page is None:
                first_page = page
            last_page = page
            buffer.append(sentence)
            buffer_tokens += tokens

        if buffer:
            chunks.append(flush(buffer, first_page, last_page))

        return chunks

    def count_tokens(self, text: str) -> int:
        """
        Counts the number of tokens in the text using the tokenizer.

        This is important for ensuring chunks of text stay within the model's limit.

        Args:
            text: The input text.

        Returns:
            The number of tokens in the input text.
        """
        return len(self.encoding.encode(text))

    def chunk_text(self, text: str, max_tokens: int = 8000) -> List[str]:
        """
        Splits long text into smaller chunks that stay under a token limit.

        Useful when sending text to models that have a maximum token limit (e.g., GPT-4 has ~8k or ~32k limits).

        Args:
            text: The cleaned input text.
            max_tokens: The maximum number of tokens allowed per chunk.

        Returns:
            A list of text chunks (strings), each within the token limit.
        """
        # Split the text into sentences using period + space as delimiter
        sentences = text.split('. ')
        chunks = []
        current_chunk = ""

        for sentence in sentences:
            # Try adding the sentence to the current chunk
            test_chunk = current_chunk + sentence + ". "

            # If this new chunk exceeds the token limit
            if self.count_tokens(test_chunk) > max_tokens and current_chunk:
                # Save the current chunk and start a new one
                chunks.append(current_chunk.strip())
                current_chunk = sentence + ". "
            else:
                # Otherwise, keep adding to the current chunk
                current_chunk = test_chunk

        # Add the final chunk if any content is left
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def get_pdf_metadata(self, pdf_file) -> Dict:
        """
        Extracts metadata from the PDF such as number of pages, title, author, and subject.

        Args:
            pdf_file: The uploaded PDF file object.

        Returns:
            A dictionary containing metadata fields or error info.
        """
        try:
            pdf_reader = PyPDF2.PdfReader(pdf_file)

            # Extract standard metadata fields if available
            metadata = {
                'num_pages': len(pdf_reader.pages),
                'title': pdf_reader.metadata.get('/Title', 'Unknown') if pdf_reader.metadata else 'Unknown',
                'author': pdf_reader.metadata.get('/Author', 'Unknown') if pdf_reader.metadata else 'Unknown',
                'subject': pdf_reader.metadata.get('/Subject', 'Unknown') if pdf_reader.metadata else 'Unknown'
            }

            return metadata
        except Exception as e:
            # If something goes wrong, return an error dict
            return {'error': str(e)}