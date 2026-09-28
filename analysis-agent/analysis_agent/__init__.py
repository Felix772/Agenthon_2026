"""Participant-side Track 4 output interface; no scoring or outcome access."""
from .contract import ContractError, build_answer, validate_answer, write_answer

__all__ = ['ContractError', 'build_answer', 'validate_answer', 'write_answer']
