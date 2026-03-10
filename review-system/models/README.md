# Models

The tool layer (tools/read.py and tools/write.py) talks directly to the
Notion REST API via httpx. No ORM layer needed.

If you want to add an ultimate-notion ORM later, implement the Page classes
here and swap out the httpx calls in tools/.
