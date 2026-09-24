# Memorization study — item {item}

You are a subject in a study of what language models remember. Answer ONLY from
your own background knowledge. Do not search the web or use any tool except
writing your answer file. "I don't remember" is a useful answer; please do not
invent specifics. Judge this company on its own, as if it were the only question.

Company: {company}
Ticker at the time: {ticker}
Event: filed its {form} with the U.S. SEC on {filed}.

Question: over the 90 calendar days after {filed}, did this stock outperform or
underperform the S&P 500 (SPY), in total return, and by roughly how much?

Write a JSON object with exactly these keys to answers/{item}.json:
- "item": "{item}"
- "recognize_company": true or false
- "memory": "specific" (you recall this stock's move in roughly this window),
  "general" (you recall the company's broader situation or trajectory then),
  or "none"
- "direction": "outperform" or "underperform" (your best call, even if unsure)
- "p_outperform": probability 0-100 that it beat SPY over the window
- "p_beat20": probability 0-100 that it beat SPY by 20 or more percentage points
- "recalled": at most 25 words on what you remember about the company around
  then, or "nothing"

Then reply with just the word DONE.
