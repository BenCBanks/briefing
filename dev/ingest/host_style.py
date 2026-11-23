"""Shared writing example and spoken style guidance for the host passes."""

HOST_STYLE_GUIDE = """Write like a good podcast host talking to one listener. Open with a listener-sized question or a clear, human reason to care, then answer it in plain words. Keep sentences short and spoken. Let each paragraph answer one question. Bridge to the next story in plain language; never say a section title or read a headline as a label. Do not speak citation markers, URLs, or publication dates as labels. Say familiar acronyms as words, not letter by letter. State an evidence gap once, plainly, only when it helps the listener. End with one sentence the listener can repeat, drawn from the script's own supported claims."""

STYLE_TARGET_EXAMPLE = """Here's a question. When you pay someone from your bank account, when does the money actually move?

With the older system, the Automated Clearing House, the honest answer is: not right away. Payments get collected into batches, and the batches are sent off together. That's why an ACH payment can take days to settle.

Instant payment systems do it differently. Each payment is handled on its own, one at a time. The bank checks it, approves it, sends it, and the receiving bank confirms it. All of that takes seconds, and it works around the clock, not just in banking hours.

So how does that compare with a wire? Wires are reliable, and they settle the same day, which is why they're used for large sums. But instant payments are always available, and they carry richer payment data, which makes it much easier for a business to match a payment to an invoice.

Here's the part that surprises people. An instant payment is final. It's a push: you send it, and it's gone. With an ACH debit, a payment can be sent back if the account turns out to be short. Instant payments don't work like that. That protects the business that gets paid, but it also means a mistake or a fraud can't simply be reversed.

What if you want to collect money, rather than send it? You can't pull it. Instead, the biller sends a request for payment. The customer approves it, and the details come pre-filled, which cuts down on typing errors.

And that finality is why security matters so much more. Because there's no undo, banks have to check for fraud and assess the other side before the money leaves. They also protect the keys that sign each payment, using specialised hardware or techniques that split a key between systems.

One more thing worth knowing: for a bank, switching this on is a big job. Upgrading a core banking system can take around eighteen months, so many banks run a new system next to the old one while they move over.

I couldn't find solid evidence on how liquidity is managed when payments settle this fast, so I'll leave that out rather than guess.

The short version: instant payments move one at a time, in seconds, and can't be taken back, which is the whole benefit and the whole risk."""
