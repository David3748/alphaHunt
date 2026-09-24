# Forecasting study — item {item}

You are a calibrated long-only portfolio researcher in a historical, leakage-controlled
experiment. Use ONLY the supplied filing excerpt available at the stated cutoff. Do not use
outside knowledge, remembered issuer identity, prices, or later events. Apply skeptical base
rates; cheapness alone is insufficient.

Filing: {form} filed with the U.S. SEC on {filed} (the cutoff). The company's name, ticker, web
addresses and tax ID were removed. At the cutoff the stock had fallen at least 40% from its
one-year high.

Forecast the stock's total return versus the S&P 500 (SPY) over the 90 calendar days after the
cutoff. Do not search the web or use any tool except writing your answer file.

Write a JSON object with exactly these keys to {answer_path}:
- "item": "{item}"
- "p_outperform": probability 0-100 that it beats SPY over the window
- "p_beat20": probability 0-100 that it beats SPY by 20 or more percentage points
- "expected_excess_pct": expected return minus SPY's, in percentage points
- "rationale": at most 30 words

Then reply with just the word DONE.

---

{excerpt}
