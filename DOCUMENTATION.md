# PDF AI Summarizer Pro - Documentation

## Overview

PDF AI Summarizer Pro is an advanced application for intelligent PDF analysis and summarization. It combines:
- **Fast Processing** with intelligent caching
- **Batch Operations** for multiple documents
- **Advanced Analytics** including sentiment analysis and readability scoring
- **Session Tracking** for usage history
- **Error Recovery** with automatic retry logic
- **Rate Limiting** to manage API usage

## Features

### Core Features
- ✅ PDF text extraction and cleaning
- ✅ Multiple summary types (brief, detailed, bullet points, executive)
- ✅ Intelligent text chunking with token counting
- ✅ Document metadata extraction
- ✅ Key quotes extraction
- ✅ CSV and text export

### Advanced Features
- ✅ **Intelligent Caching**: Avoid reprocessing identical documents
- ✅ **Batch Processing**: Process multiple PDFs in one session
- ✅ **Session History**: Track all processed documents
- ✅ **Keyword Extraction**: Identify top topics
- ✅ **Sentiment Analysis**: Understand document tone
- ✅ **Readability Scoring**: Assess text complexity
- ✅ **Processing Metrics**: Track efficiency and compression ratios
- ✅ **Error Recovery**: Automatic retry with exponential backoff
- ✅ **Rate Limiting**: Manage API call frequency
- ✅ **File Validation**: Comprehensive input checking

## Installation

### Prerequisites
- Python 3.8+
- pip

### Setup

1. Clone the repository
```bash
cd PDF-Analyzer
```

2. Create virtual environment
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # macOS/Linux
```

3. Install dependencies
```bash
pip install -r requirements.txt
```

4. Create `.env` file
```bash
OPENROUTER_API_KEY=your_api_key_here
LOG_LEVEL=INFO
```

## Configuration

Edit `config.py` to customize:

### Processing
- `MAX_TOKENS_PER_CHUNK`: Token limit per chunk (default: 8000)
- `SUPPORTED_SUMMARY_TYPES`: Available summary types
- `ENABLE_PARALLEL_PROCESSING`: Parallel chunk processing

### Caching
- `ENABLE_CACHING`: Enable/disable caching (default: True)
- `CACHE_EXPIRATION_HOURS`: Cache validity period (default: 24)
- `CACHE_DIR`: Cache storage directory

### Session Management
- `ENABLE_SESSION_HISTORY`: Track processing history (default: True)
- `MAX_SESSION_HISTORY_ITEMS`: Max sessions to keep (default: 50)

### API Limits
- `API_TIMEOUT_SECONDS`: Request timeout (default: 60)
- `API_MAX_RETRIES`: Retry attempts (default: 3)
- `RATE_LIMIT_REQUESTS_PER_MINUTE`: Rate limit (default: 60)

### File Validation
- `MAX_FILE_SIZE_MB`: Maximum file size (default: 50)
- `MIN_FILE_SIZE_BYTES`: Minimum file size (default: 1024)

## Usage

### Running the Application

```bash
streamlit run app_enhanced.py
```

### Single File Processing
1. Upload a PDF file
2. Configure processing settings
3. Enable desired features (keywords, sentiment, etc.)
4. Click "Process PDF"
5. Download results as text or CSV

### Batch Processing
1. Enable "Batch Mode" in sidebar
2. Upload multiple PDF files
3. Click "Process All Files"
4. Results are cached for future reference

### API Configuration

The application uses OpenRouter API. Get your API key from https://openrouter.ai/

Set the key in `.env`:
```
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxx
```

## Module Overview

### Core Modules

#### `pdf_processor.py`
Handles PDF extraction, text cleaning, and tokenization
- `extract_text_from_pdf()`: Extract text from PDF
- `clean_text()`: Remove noise and normalize text
- `chunk_text()`: Split text into manageable chunks
- `count_tokens()`: Estimate token count

#### `summarizer.py`
Generates summaries using LLM
- `summarize_chunks()`: Process chunks and generate summaries
- `analyze_document_structure()`: Extract document insights
- `extract_key_quotes()`: Find important quotes

#### `utils.py`
Utility functions for UI and data processing
- `validate_api_key()`: Check API key validity
- `format_summary_display()`: Format summaries for display
- `export_summary_to_text()`: Export as text file
- `create_summary_dataframe()`: Convert to pandas DataFrame

### Advanced Modules

#### `cache_manager.py`
Intelligent caching system
```python
from cache_manager import cache_manager

# Retrieve cached result
result = cache_manager.get(filename, file_hash, summary_type)

# Store result
cache_manager.set(filename, file_hash, summary_type, data)

# Clear expired
cache_manager.clear_expired()
```

#### `session_manager.py`
Session and usage tracking
```python
from session_manager import session_manager

# Add session
session_manager.add_session(filename, size, type, time, chunks)

# Get history
history = session_manager.get_history(limit=10)

# Statistics
stats = session_manager.get_session_stats()
```

#### `validators.py`
Input validation and rate limiting
```python
from validators import file_validator, rate_limiter

# Validate file
is_valid, msg = file_validator.validate_file(file)

# Check rate limit
if rate_limiter.is_allowed():
    process()
```

#### `advanced_features.py`
Advanced text analysis
```python
from advanced_features import (
    keyword_extractor,
    sentiment_analyzer,
    readability_analyzer
)

keywords = keyword_extractor.extract_keywords(text)
sentiment = sentiment_analyzer.analyze_sentiment(text)
readability = readability_analyzer.calculate_readability_score(text)
```

#### `error_handler.py`
Error handling and retry logic
```python
from error_handler import retry_with_backoff

@retry_with_backoff(max_retries=3)
def api_call():
    pass
```

#### `config.py`
Centralized configuration management
```python
from config import Config

print(Config.MAX_FILE_SIZE_MB)
Config.validate()
```

#### `logger_config.py`
Logging setup
```python
from logger_config import setup_logger

logger = setup_logger(__name__)
logger.info("Processing started")
```

## API Reference

### PDFProcessor
```python
processor = PDFProcessor()

# Extract and process
text = processor.extract_text_from_pdf(file)
clean = processor.clean_text(text)
chunks = processor.chunk_text(clean, max_tokens=8000)
tokens = processor.count_tokens(text)
metadata = processor.get_pdf_metadata(file)
```

### PDFSummarizer
```python
summarizer = PDFSummarizer()

# Generate summaries
results = summarizer.summarize_chunks(chunks, "detailed")

# Analysis
analysis = summarizer.analyze_document_structure(text)
quotes = summarizer.extract_key_quotes(text)
```

### KeywordExtractor
```python
keywords = keyword_extractor.extract_keywords(
    text,
    num_keywords=20,
    min_word_length=4
)
```

### SentimentAnalyzer
```python
sentiment = sentiment_analyzer.analyze_sentiment(text)
# Returns: {sentiment, score, positive_words, negative_words}
```

### ReadabilityAnalyzer
```python
readability = readability_analyzer.calculate_readability_score(text)
# Returns: {reading_level, complexity_score, avg_words_per_sentence, avg_word_length}
```

## Testing

Run the test suite:
```bash
pytest test_app.py -v
```

Test categories:
- Cache management tests
- Session management tests
- File validation tests
- Rate limiting tests
- Keyword extraction tests
- Sentiment analysis tests
- Readability analysis tests
- Error handling tests

## Troubleshooting

### API Key Issues
**Error**: "OPENROUTER_API_KEY not set"
- Solution: Set `OPENROUTER_API_KEY` in `.env` file

### Rate Limiting
**Error**: "Rate limit exceeded"
- Solution: Wait before making new requests. Limit is 60 requests/minute by default.

### Cache Issues
**Error**: "Cache corruption"
- Solution: Run cache cleanup in UI, or delete `.cache` directory

### Large Files
**Error**: "File is too large"
- Solution: Increase `MAX_FILE_SIZE_MB` in config.py or compress PDF

### Timeout Errors
**Error**: "Request timed out"
- Solution: Increase `API_TIMEOUT_SECONDS` or split large documents

## Performance Optimization

### For Faster Processing
1. Enable caching to avoid reprocessing
2. Reduce `MAX_TOKENS_PER_CHUNK` for quicker processing
3. Use "brief" summary type instead of "detailed"
4. Enable parallel processing in config

### For Better Accuracy
1. Increase `MAX_TOKENS_PER_CHUNK` to 10000
2. Use "detailed" summary type
3. Enable document analysis
4. Extract quotes for context

## Security Considerations

1. **API Key**: Never commit `.env` files to version control
2. **File Access**: Ensure proper file permissions on `.cache` directory
3. **Logging**: Review `logs/` directory for sensitive information
4. **Session Data**: Clear history when sharing computers

## Contributing

To contribute improvements:
1. Create a feature branch
2. Make changes and add tests
3. Update documentation
4. Submit pull request

## License

[Your License Here]

## Support

For issues, questions, or suggestions:
- Check troubleshooting section
- Review application logs in `logs/` directory
- Consult configuration in `config.py`

## Changelog

### v2.0 (Current)
- Added intelligent caching system
- Implemented batch processing
- Added advanced analytics (sentiment, readability)
- Session tracking and history
- Error recovery with retry logic
- Comprehensive testing suite
- Enhanced UI with dark mode support

### v1.0
- Basic PDF summarization
- Multiple summary types
- Export functionality
- Quote extraction

## Future Enhancements

- [ ] Multi-language support
- [ ] GPU acceleration for faster processing
- [ ] Web API endpoint
- [ ] Database backend for session storage
- [ ] Custom prompt templates
- [ ] Integration with cloud storage
- [ ] Real-time collaboration features
- [ ] Advanced NLP models
