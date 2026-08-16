"""
Unit tests for PDF Analyzer
Run with: pytest test_app.py -v
"""
import pytest
import os
import json
from datetime import datetime
from cache_manager import CacheManager
from session_manager import SessionManager
from validators import FileValidator, RateLimiter
from advanced_features import (
    KeywordExtractor, SentimentAnalyzer,
    ReadabilityAnalyzer, ProcessingMetrics
)
from error_handler import ErrorHandler, RateLimitError


class TestCacheManager:
    """Test cache management functionality"""

    def test_cache_key_generation(self):
        """Test cache key generation"""
        key1 = CacheManager._generate_cache_key("test.pdf", "hash123", "brief")
        key2 = CacheManager._generate_cache_key("test.pdf", "hash123", "brief")
        assert key1 == key2, "Same inputs should generate same key"

    def test_cache_set_and_get(self):
        """Test setting and retrieving cached data"""
        cm = CacheManager()
        data = {"summary": "test summary", "status": "success"}
        
        cm.set("test.pdf", "hash123", "brief", data)
        retrieved = cm.get("test.pdf", "hash123", "brief")
        
        assert retrieved is not None
        assert retrieved == data

    def test_cache_expiration(self):
        """Test cache expiration"""
        cm = CacheManager()
        
        # Manually set expired cache entry
        cache_key = cm._generate_cache_key("test.pdf", "hash", "brief")
        cache_file = os.path.join(cm.cache_dir, f"{cache_key}.json")
        
        expired_data = {
            "file_name": "test.pdf",
            "file_hash": "hash",
            "summary_type": "brief",
            "created_at": "2020-01-01T00:00:00",  # Old date
            "data": {"summary": "test"}
        }
        
        with open(cache_file, "w") as f:
            json.dump(expired_data, f)
        
        # Should return None for expired cache
        result = cm.get("test.pdf", "hash", "brief")
        assert result is None


class TestSessionManager:
    """Test session management functionality"""

    def test_add_session(self):
        """Test adding session to history"""
        sm = SessionManager()
        sm.clear_history()
        
        success = sm.add_session(
            file_name="test.pdf",
            file_size=1024,
            summary_type="brief",
            processing_time=5.0,
            num_chunks=3
        )
        
        assert success
        history = sm.get_history()
        assert len(history) == 1
        assert history[0]["file_name"] == "test.pdf"

    def test_session_stats(self):
        """Test getting session statistics"""
        sm = SessionManager()
        sm.clear_history()
        
        sm.add_session("test1.pdf", 1024, "brief", 5.0, 3)
        sm.add_session("test2.pdf", 2048, "detailed", 10.0, 5)
        
        stats = sm.get_session_stats()
        assert stats["total_sessions"] == 2
        assert stats["success_rate"] == 100.0

    def test_clear_history(self):
        """Test clearing session history"""
        sm = SessionManager()
        sm.add_session("test.pdf", 1024, "brief", 5.0, 3)
        
        sm.clear_history()
        history = sm.get_history()
        assert len(history) == 0


class TestFileValidator:
    """Test file validation"""

    def test_validate_file_extension(self):
        """Test file extension validation"""
        # Mock file object
        class MockFile:
            name = "test.pdf"
            def seek(self, pos, whence=0):
                pass
            def tell(self):
                return 5000000  # 5MB
        
        file = MockFile()
        is_valid, message = FileValidator.validate_file(file)
        assert is_valid

    def test_validate_file_size_too_large(self):
        """Test file size validation (too large)"""
        class MockFile:
            name = "test.pdf"
            def seek(self, pos, whence=0):
                pass
            def tell(self):
                return 100 * 1024 * 1024  # 100MB
        
        file = MockFile()
        is_valid, message = FileValidator.validate_file(file)
        assert not is_valid
        assert "too large" in message.lower()


class TestRateLimiter:
    """Test rate limiting"""

    def test_rate_limit_allowed(self):
        """Test rate limit allows requests"""
        limiter = RateLimiter(max_requests=5)
        
        for i in range(5):
            assert limiter.is_allowed()
        
        assert not limiter.is_allowed()

    def test_rate_limit_window(self):
        """Test rate limit resets after time window"""
        limiter = RateLimiter(max_requests=1)
        
        assert limiter.is_allowed()
        assert not limiter.is_allowed()
        
        # Clear old requests (simulating time passing)
        limiter.request_times = []
        assert limiter.is_allowed()


class TestKeywordExtractor:
    """Test keyword extraction"""

    def test_extract_keywords(self):
        """Test keyword extraction from text"""
        text = "python programming is great python code python language"
        keywords = KeywordExtractor.extract_keywords(text, num_keywords=3)
        
        assert len(keywords) <= 3
        assert "python" in keywords or "programming" in keywords

    def test_extract_keywords_with_stopwords(self):
        """Test that stop words are filtered"""
        text = "the and a or in on at to for of"
        keywords = KeywordExtractor.extract_keywords(text)
        
        # Should be empty or very few since all are stop words
        assert len(keywords) <= 2

    def test_urls_do_not_become_keywords(self):
        """A link must not contribute its scheme, host or path as keywords.

        Extracted PDF text carries links verbatim. Tokenising one yields
        "https", the host and every path segment, and on a short document
        those out-rank the actual subject on frequency alone.
        """
        text = (
            "Radiology staffing agreement for the clinic. "
            "Apply at https://careers.example.com/apply?jobSeqNo=12345 "
            "or visit www.example.com/careers/openings "
            "or email recruiting@example.com for details."
        )
        keywords = KeywordExtractor.extract_keywords(text)

        for noise in ("https", "careers", "example", "jobseqno", "recruiting"):
            assert noise not in keywords, f"{noise!r} leaked in from a link"
        assert "radiology" in keywords
        assert "staffing" in keywords

    def test_plain_words_matching_url_parts_still_count(self):
        """Only actual links are stripped, not words that resemble them."""
        text = "The clinic email policy governs email retention and email review."
        keywords = KeywordExtractor.extract_keywords(text)

        assert "email" in keywords


class TestSentimentAnalyzer:
    """Test sentiment analysis"""

    def test_positive_sentiment(self):
        """Test positive sentiment detection"""
        text = "This is amazing and wonderful and excellent work"
        sentiment = SentimentAnalyzer.analyze_sentiment(text)
        
        assert sentiment["sentiment"] == "positive"
        assert sentiment["score"] > 0
        assert sentiment["positive_words"] > 0

    def test_negative_sentiment(self):
        """Test negative sentiment detection"""
        text = "This is terrible and awful and horrible"
        sentiment = SentimentAnalyzer.analyze_sentiment(text)
        
        assert sentiment["sentiment"] == "negative"
        assert sentiment["score"] < 0
        assert sentiment["negative_words"] > 0

    def test_neutral_sentiment(self):
        """Test neutral sentiment detection"""
        text = "The document contains information"
        sentiment = SentimentAnalyzer.analyze_sentiment(text)
        
        assert sentiment["sentiment"] == "neutral"


class TestReadabilityAnalyzer:
    """Test readability analysis"""

    def test_readability_score(self):
        """Test readability score calculation"""
        text = "The quick brown fox jumps. It runs fast. " * 10
        score = ReadabilityAnalyzer.calculate_readability_score(text)
        
        assert "reading_level" in score
        assert "complexity_score" in score
        assert score["complexity_score"] >= 0

    def test_readability_empty_text(self):
        """Test readability with empty text"""
        text = ""
        score = ReadabilityAnalyzer.calculate_readability_score(text)
        
        assert score["reading_level"] == "Unknown"


class TestProcessingMetrics:
    """Test processing metrics calculation"""

    def test_calculate_metrics(self):
        """Test metrics calculation"""
        original = "a" * 1000
        summary = "a" * 100
        metrics = ProcessingMetrics.calculate_metrics(original, summary, 5.0, 10)
        
        assert metrics["compression_ratio"] == 10.0
        assert metrics["processing_time_seconds"] == 5.0
        assert metrics["chunks_processed"] == 10

    def test_metrics_division_by_zero(self):
        """An empty summary must not raise; the ratio is undefined, so it is
        reported as 0 rather than invented."""
        original = "a" * 1000
        summary = ""
        metrics = ProcessingMetrics.calculate_metrics(original, summary, 5.0, 10)

        assert metrics["compression_ratio"] == 0
        assert metrics["summary_characters"] == 0
        assert metrics["original_characters"] == 1000


class TestErrorHandler:
    """Test error handling"""

    def test_handle_rate_limit_error(self):
        """Test rate limit error handling"""
        error = RateLimitError("Too many requests")
        message = ErrorHandler.handle_api_error(error, "test")
        
        assert "rate limit" in message.lower()

    def test_handle_generic_error(self):
        """Test generic error handling"""
        error = ValueError("Test error")
        message = ErrorHandler.handle_api_error(error, "test")
        
        assert "error" in message.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
