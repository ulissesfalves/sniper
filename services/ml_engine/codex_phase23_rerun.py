from __future__ import annotations

import asyncio
import logging

import structlog

import phase4_cpcv

phase4_cpcv.main = lambda: None

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    processors=[
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

import main

if __name__ == '__main__':
    result = asyncio.run(main.run_ml_pipeline_full())
    print(result)
