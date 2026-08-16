"""
Advanced features for PDF Analyzer:
- Keyword extraction
- Sentiment analysis
- Readability scoring
- Document insights
- Processing metrics
"""
import re
from typing import List, Dict, Any, Optional
from collections import Counter
from logger_config import setup_logger

logger = setup_logger(__name__)


class KeywordExtractor:
    """Extracts keywords from text"""

    # Common stop words to exclude
    STOP_WORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "up", "about", "into", "through", "during",
        "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "do", "does", "did", "will", "would", "could", "should", "may", "might",
        "can", "this", "that", "these", "those", "i", "you", "he", "she", "it",
        "we", "they", "what", "which", "who", "when", "where", "why", "how"
    }

    # Links survive text extraction intact, and splitting one into words yields
    # "https", the host and every path segment — which then out-rank the real
    # subject of the document on frequency alone. Drop them before tokenising.
    URL_RE = re.compile(r'(?:https?://|www\.|mailto:)\S+', re.IGNORECASE)
    EMAIL_RE = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')

    @staticmethod
    def extract_keywords(text: str, num_keywords: int = 20, min_word_length: int = 4) -> List[str]:
        """
        Extract top keywords from text

        Args:
            text: Input text to extract keywords from
            num_keywords: Number of keywords to return
            min_word_length: Minimum word length to consider

        Returns:
            List of keywords
        """
        try:
            # Strip links and addresses so their fragments cannot become keywords.
            cleaned = KeywordExtractor.URL_RE.sub(" ", text)
            cleaned = KeywordExtractor.EMAIL_RE.sub(" ", cleaned)

            # Convert to lowercase and remove special characters
            words = re.findall(r'\b[a-z]+(?:\'[a-z]+)?\b', cleaned.lower())

            # Filter stop words and short words
            filtered_words = [
                w for w in words
                if w not in KeywordExtractor.STOP_WORDS and len(w) >= min_word_length
            ]

            # Get most common words
            word_counts = Counter(filtered_words)
            keywords = [word for word, _ in word_counts.most_common(num_keywords)]

            logger.debug(f"Extracted {len(keywords)} keywords from text")
            return keywords

        except Exception as e:
            logger.error(f"Error extracting keywords: {str(e)}")
            return []


class SentimentAnalyzer:
    """Simple sentiment analysis using keyword matching"""

    POSITIVE_WORDS = {
        "good", "great", "excellent", "amazing", "wonderful", "positive", "success",
        "happy", "love", "best", "perfect", "brilliant", "fantastic", "outstanding"
    }

    NEGATIVE_WORDS = {
        "bad", "terrible", "awful", "horrible", "negative", "failure", "sad",
        "hate", "worst", "poor", "useless", "disgusting", "disappointing"
    }

    @staticmethod
    def analyze_sentiment(text: str) -> Dict[str, Any]:
        """
        Analyze sentiment of text (basic lexicon-based approach)
        
        Args:
            text: Input text
        
        Returns:
            Dictionary with sentiment scores
        """
        try:
            words = set(re.findall(r'\b[a-z]+\b', text.lower()))

            positive_count = len(words & SentimentAnalyzer.POSITIVE_WORDS)
            negative_count = len(words & SentimentAnalyzer.NEGATIVE_WORDS)
            total = positive_count + negative_count

            if total == 0:
                sentiment = "neutral"
                score = 0.0
            else:
                score = (positive_count - negative_count) / total
                if score > 0.1:
                    sentiment = "positive"
                elif score < -0.1:
                    sentiment = "negative"
                else:
                    sentiment = "neutral"

            return {
                "sentiment": sentiment,
                "score": round(score, 2),
                "positive_words": positive_count,
                "negative_words": negative_count
            }

        except Exception as e:
            logger.error(f"Error analyzing sentiment: {str(e)}")
            return {"sentiment": "unknown", "score": 0.0, "positive_words": 0, "negative_words": 0}


class ReadabilityAnalyzer:
    """Analyzes text readability"""

    @staticmethod
    def calculate_readability_score(text: str) -> Dict[str, Any]:
        """
        Calculate readability metrics
        
        Args:
            text: Input text
        
        Returns:
            Dictionary with readability metrics
        """
        try:
            # Basic metrics
            sentences = re.split(r'[.!?]+', text)
            sentences = [s.strip() for s in sentences if s.strip()]
            
            words = text.split()
            words_count = len(words)
            sentences_count = len(sentences)
            
            if sentences_count == 0 or words_count == 0:
                return {
                    "avg_words_per_sentence": 0,
                    "avg_word_length": 0,
                    "reading_level": "Unknown",
                    "complexity_score": 0
                }

            avg_words_per_sentence = words_count / sentences_count
            avg_word_length = sum(len(w) for w in words) / words_count

            # Flesch Kincaid Grade Level (simplified)
            complexity_score = (
                0.39 * avg_words_per_sentence +
                11.8 * (sum(len(w) for w in words if len(w) > 6) / words_count) -
                15.59
            )
            complexity_score = max(0, min(18, complexity_score))  # Clamp to 0-18

            if complexity_score < 6:
                reading_level = "Very Easy"
            elif complexity_score < 9:
                reading_level = "Easy"
            elif complexity_score < 12:
                reading_level = "Medium"
            elif complexity_score < 14:
                reading_level = "Hard"
            else:
                reading_level = "Very Hard"

            return {
                "avg_words_per_sentence": round(avg_words_per_sentence, 2),
                "avg_word_length": round(avg_word_length, 2),
                "reading_level": reading_level,
                "complexity_score": round(complexity_score, 2)
            }

        except Exception as e:
            logger.error(f"Error calculating readability: {str(e)}")
            return {
                "avg_words_per_sentence": 0,
                "avg_word_length": 0,
                "reading_level": "Unknown",
                "complexity_score": 0
            }


class ProcessingMetrics:
    """Tracks processing metrics and statistics"""

    @staticmethod
    def calculate_metrics(
        original_text: str,
        summary_text: str,
        processing_time: float,
        num_chunks: int
    ) -> Dict[str, Any]:
        """
        Calculate processing metrics
        
        Args:
            original_text: Original document text
            summary_text: Generated summary
            processing_time: Time taken to process
            num_chunks: Number of chunks processed
        
        Returns:
            Dictionary with metrics
        """
        try:
            original_length = len(original_text)
            summary_length = len(summary_text)
            compression_ratio = original_length / summary_length if summary_length > 0 else 0

            original_words = len(original_text.split())
            summary_words = len(summary_text.split())

            return {
                "original_characters": original_length,
                "summary_characters": summary_length,
                "compression_ratio": round(compression_ratio, 2),
                "original_words": original_words,
                "summary_words": summary_words,
                "processing_time_seconds": round(processing_time, 2),
                "chunks_processed": num_chunks,
                "avg_time_per_chunk": round(processing_time / num_chunks, 3) if num_chunks > 0 else 0
            }

        except Exception as e:
            logger.error(f"Error calculating metrics: {str(e)}")
            return {
                "original_characters": 0,
                "summary_characters": 0,
                "compression_ratio": 0,
                "original_words": 0,
                "summary_words": 0,
                "processing_time_seconds": 0,
                "chunks_processed": 0,
                "avg_time_per_chunk": 0
            }


class DocumentInsights:
    """Provides insights about documents"""

    @staticmethod
    def generate_insights(text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate comprehensive document insights
        
        Args:
            text: Document text
            metadata: Document metadata
        
        Returns:
            Dictionary with insights
        """
        try:
            keywords = KeywordExtractor.extract_keywords(text, num_keywords=10)
            sentiment = SentimentAnalyzer.analyze_sentiment(text)
            readability = ReadabilityAnalyzer.calculate_readability_score(text)

            return {
                "num_pages": metadata.get("num_pages", 0),
                "text_length": len(text),
                "word_count": len(text.split()),
                "top_keywords": keywords,
                "sentiment": sentiment,
                "readability": readability,
                "language": "English"  # Could be enhanced with language detection
            }

        except Exception as e:
            logger.error(f"Error generating insights: {str(e)}")
            return {}


# Global instances
keyword_extractor = KeywordExtractor()
sentiment_analyzer = SentimentAnalyzer()
readability_analyzer = ReadabilityAnalyzer()
processing_metrics = ProcessingMetrics()
document_insights = DocumentInsights()
