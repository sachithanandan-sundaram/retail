"""Retail LLM natural-language query demo.

Pipeline:  question -> [fast path?] -> LLM writes SQL -> validate/repair
           -> run against SQLite -> [self-correction check] -> LLM phrases answer.
"""
