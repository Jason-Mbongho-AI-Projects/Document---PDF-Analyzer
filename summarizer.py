import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from typing import List, Dict, Iterator
import time

import prompt_guard

load_dotenv()

class PDFSummarizer:
    def __init__(self):
        self.llm = ChatOpenAI(
            model="openai/gpt-4.1-mini",
            temperature=0.3,
            openai_api_key=os.getenv("OPENROUTER_API_KEY"),
            openai_api_base="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/niti007/pdfsummarizer",
                "X-Title": "PDF Summarizer",
            },
        )

        self.summary_prompts = {
            'brief': PromptTemplate(
                input_variables=["text"],
                template="""
Provide a brief, concise summary of the following text in 2-3 sentences.
Focus on the main points and key takeaways.

Text: {text}

Brief Summary:"""
            ),
            'detailed': PromptTemplate(
                input_variables=["text"],
                template="""
Provide a detailed summary of the following text.
Include:
- Main topics and themes
- Key arguments or findings
- Important details and examples
- Conclusions or recommendations

Text: {text}

Detailed Summary:"""
            ),
            'bullet_points': PromptTemplate(
                input_variables=["text"],
                template="""
Summarize the following text as clear, organized bullet points.
Group related information together and use sub-bullets where appropriate.

Text: {text}

Bullet Point Summary:"""
            ),
            'executive': PromptTemplate(
                input_variables=["text"],
                template="""
Create an executive summary of the following text suitable for business leaders.
Include:
- Key insights and findings
- Strategic implications
- Actionable recommendations
- Risk factors or considerations

Text: {text}

Executive Summary:"""
            )
        }

    def summarize_text(self, text: str, summary_type: str = 'detailed') -> str:
        if summary_type not in self.summary_prompts:
            summary_type = 'detailed'

        chain = self.summary_prompts[summary_type] | self.llm | StrOutputParser()

        try:
            summary = chain.invoke({"text": prompt_guard.fence(text)})
            return summary
        except Exception as e:
            return f"Error generating summary: {str(e)}"

    def stream_summary(self, text: str, summary_type: str = 'detailed') -> Iterator[str]:
        """Yield a summary incrementally, one token delta at a time."""
        if summary_type not in self.summary_prompts:
            summary_type = 'detailed'

        chain = self.summary_prompts[summary_type] | self.llm | StrOutputParser()

        try:
            for delta in chain.stream({"text": prompt_guard.fence(text)}):
                yield delta
        except Exception as e:
            yield f"Error generating summary: {str(e)}"

    def stream_combined_summary(self, chunk_summaries: List[Dict], summary_type: str) -> Iterator[str]:
        """Yield the final synthesis incrementally.

        Falls back to emitting the concatenated section summaries if the meta
        call fails, matching combine_summaries' behaviour.
        """
        individual_summaries = [cs['summary'] for cs in chunk_summaries if 'Error' not in cs['summary']]
        if not individual_summaries:
            yield "No valid summaries were generated."
            return

        combined_text = "\n\n".join(
            [f"Section {i+1}: {summary}" for i, summary in enumerate(individual_summaries)]
        )

        # A single section has nothing to synthesise across.
        if len(individual_summaries) == 1:
            yield individual_summaries[0]
            return

        meta_chain = self._meta_prompt() | self.llm | StrOutputParser()

        emitted = False
        try:
            for delta in meta_chain.stream({"summaries": prompt_guard.fence(combined_text), "summary_type": summary_type}):
                emitted = True
                yield delta
        except Exception:
            if not emitted:
                yield f"Combined Summary:\n\n{combined_text}"

    @staticmethod
    def _meta_prompt() -> PromptTemplate:
        return PromptTemplate(
            input_variables=["summaries", "summary_type"],
            template="""
You have been provided with summaries from different sections of a document.
Create a final, cohesive summary that synthesizes all the information.

Summary Type: {summary_type}

Section Summaries:
{summaries}

Final Cohesive Summary:"""
        )

    def summarize_chunks(self, chunks: List[str], summary_type: str = 'detailed') -> Dict:
        chunk_summaries = []

        print(f"Processing {len(chunks)} chunks...")
        for i, chunk in enumerate(chunks):
            print(f"Summarizing chunk {i+1}/{len(chunks)}...")
            try:
                summary = self.summarize_text(chunk, summary_type)
                chunk_summaries.append({
                    'chunk_number': i + 1,
                    'summary': summary,
                    'original_length': len(chunk),
                    'summary_length': len(summary)
                })
                if i < len(chunks) - 1:
                    time.sleep(1)  # rate limit control
            except Exception as e:
                chunk_summaries.append({
                    'chunk_number': i + 1,
                    'summary': f"Error processing chunk: {str(e)}",
                    'original_length': len(chunk),
                    'summary_length': 0
                })

        combined_summary = self.combine_summaries(chunk_summaries, summary_type)

        return {
            'individual_summaries': chunk_summaries,
            'combined_summary': combined_summary,
            'total_chunks': len(chunks),
            'summary_type': summary_type
        }

    def combine_summaries(self, chunk_summaries: List[Dict], summary_type: str) -> str:
        individual_summaries = [cs['summary'] for cs in chunk_summaries if 'Error' not in cs['summary']]
        if not individual_summaries:
            return "No valid summaries were generated."

        combined_text = "\n\n".join([f"Section {i+1}: {summary}" for i, summary in enumerate(individual_summaries)])

        meta_chain = self._meta_prompt() | self.llm | StrOutputParser()

        try:
            final_summary = meta_chain.invoke({"summaries": prompt_guard.fence(combined_text), "summary_type": summary_type})
            return final_summary
        except Exception as e:
            return f"Combined Summary:\n\n{combined_text}"

    def analyze_document_structure(self, text: str) -> Dict:
        analysis_prompt = PromptTemplate(
            input_variables=["text"],
            template="""
Analyze the structure and content of this document. Provide:

1. Document Type (research paper, report, article, etc.)
2. Main Topics/Themes
3. Key Sections or Chapters
4. Target Audience
5. Overall Purpose

Text: {text}

Document Analysis:"""
        )

        analysis_chain = analysis_prompt | self.llm | StrOutputParser()

        try:
            analysis = analysis_chain.invoke({"text": prompt_guard.fence(text[:3000])})
            return {'analysis': analysis, 'status': 'success'}
        except Exception as e:
            return {'analysis': f"Error analyzing document: {str(e)}", 'status': 'error'}

    def extract_key_quotes(self, text: str) -> List[str]:
        quotes_prompt = PromptTemplate(
            input_variables=["text"],
            template="""
Extract 5-10 key quotes, statements, or important phrases from this text.
Choose quotes that best represent the main ideas or are particularly insightful.

Text: {text}

Key Quotes (one per line):"""
        )

        quotes_chain = quotes_prompt | self.llm | StrOutputParser()

        try:
            quotes_response = quotes_chain.invoke({"text": prompt_guard.fence(text)})
            quotes = [quote.strip() for quote in quotes_response.split('\n') if quote.strip()]
            return quotes[:10]
        except Exception as e:
            return [f"Error extracting quotes: {str(e)}"]