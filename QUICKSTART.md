# Quick Start Guide - PDF AI Summarizer Pro

## 5-Minute Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure API Key
Create `.env` file:
```
OPENROUTER_API_KEY=your_key_here
LOG_LEVEL=INFO
```

Get API key from: https://openrouter.ai/

### 3. Run Application
```bash
streamlit run app_enhanced.py
```

The app will open at `http://localhost:8501`

## Basic Usage

### Single File Summary
1. Upload a PDF
2. Select summary type (Brief, Detailed, Bullet Points, Executive)
3. Click "Process PDF"
4. Download results

### Batch Processing
1. Enable "Batch Mode" in Settings
2. Upload multiple PDFs
3. Click "Process All Files"
4. All results are cached

### View History
1. Go to "Storage & Cache" in sidebar
2. Click "View Session Stats"
3. See processing history and success rate

## Key Features

### 🚀 Caching
- First time: PDF is processed
- Second time: Result loaded from cache (instant!)
- Disabled by default? Toggle in Settings

### 📦 Batch Processing
- Upload multiple PDFs
- Process all at once
- Results cached for future use

### 🔍 Advanced Analytics
- Keywords extraction
- Sentiment analysis
- Readability scoring
- Processing metrics

### 💾 Session History
- All processed files tracked
- Performance statistics
- Success rate monitoring

### 🔄 Error Recovery
- Automatic retries
- Rate limiting protection
- Timeout handling
- Helpful error messages

## Configuration

### Settings in Sidebar

**Processing Settings**
- Summary Type: Choose output format
- Max Tokens: Quality vs speed trade-off
- Lower tokens = faster, less detailed
- Higher tokens = slower, more detailed

**Advanced Features**
- Toggle individual features on/off
- Document Analysis: Extract structure
- Keywords: Top topics
- Sentiment: Tone analysis
- Readability: Text complexity

**Storage & Cache**
- Enable/disable caching
- Clear old cache entries
- View session statistics

## File Size Limits

- **Minimum**: 1 KB
- **Maximum**: 50 MB
- Adjust in `config.py` if needed

## Troubleshooting

### "API Key not found"
→ Add `OPENROUTER_API_KEY` to `.env`

### "Rate limit exceeded"
→ Wait a few seconds, then try again

### "File too large"
→ Compress PDF or increase `MAX_FILE_SIZE_MB` in config

### "Processing times out"
→ Increase `API_TIMEOUT_SECONDS` in config

## Tips & Tricks

1. **Faster processing**: Disable features you don't need
2. **Better caching**: Process same files multiple times
3. **Batch efficiency**: Upload similar document types together
4. **Cost control**: Monitor session stats for API usage
5. **Export data**: Download results as CSV for analysis

## Next Steps

- Read [DOCUMENTATION.md](DOCUMENTATION.md) for full API reference
- Check [config.py](config.py) for advanced configuration
- Run tests: `pytest test_app.py`
- Review logs in `logs/` directory for debugging

## Support

- Check DOCUMENTATION.md for detailed guides
- Review `logs/pdf_analyzer.log` for errors
- Inspect `.cache/` for cached results
- Check `.session_history/` for usage data

---

**Version**: 2.0
**Last Updated**: 2024
**Status**: Production Ready
