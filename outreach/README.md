# Letters

Requests sent on the site's behalf, kept as a record (the family's letters log: `family/LETTERS.md` in the charity's
repository). Each starts with its To: and Subject: lines, and is sent from the charity's Gmail account by
`deploy/mail.sh` (docs/HOSTING.md, "Email from the server"). Send a test copy to yourself first, then the letter:

    bash /opt/hansardgpt/deploy/mail.sh send --test-to <your address> /opt/hansardgpt/outreach/2026-11/mysociety.txt
    bash /opt/hansardgpt/deploy/mail.sh send /opt/hansardgpt/outreach/2026-11/mysociety.txt

A letter already sent is skipped. Replies arrive in the account's inbox; record them in docs/STATUS.md and the
family's LETTERS.md and LICENCES.md.
