# The Idea of PaperExplorer
is to allow users to explore a paper.
Follow its sources as traces into the territory of ideas and knowledge.
Opposing the Zeitgeist of fast and shallow results. The ones which have been judged, evaluated, summarised and hallucinated by GPT based Agentic Intelligence.
The amazing technology of being able to generate, retrieve, pattern match text in such a sophisticated way is impressive and massively usefull.
However instead of focusing on the next skill how claude can summarize you 50 papers at once this asks how could we read a paper if we could natively navigate through it. Seeing what was really said by the authors before. Following ideas in a review paper. And then we could build a context that allows us to structurly work with what we have. Make notes, Q&A papers.
Clonkers will do certain things in the future but to keep the human relevant is to give him the role of the explorer. Generating and evaluating ideas.
Guiding the headlight through the dark.

# Workflow
*This workflow aims to use as little text generating AI as possible and build a solid base to build on*
1. Start with a single paper already in a zotero database within a collection
2. Parse the paper through locallama
3. Index it into a database
	1. Hosted postgres on docker
	2. relational database
	3. Stores chunks and their meta information
	4. create embeddings
4. Extract a bibliography from the original pdf / the processed one
5. resolve all the sources and add them to the zotero collection
6. resolve sources for the collection for full text versions
7. transform full text version using locallama
	1. Index into the same database as above
8. extract all inline citations and their sourrounding context (chunk(s) they are contained in)
9. For each one retrieve the top 5 closest matching chunks
10. Creaete an html interface that can be used to explore a nicely rendered version of the paper with a clickable interface that shows me the 5 selected chunks
# Technical Base
*I use these projects to teach myself certain techniques by applying them*
1. Postgres database via docker
	1. train my docker usage
	2. train postgres and sql usage
	3. modify postgres for things like vector search
	4. do hybrid retrieval over the database with metadata filters
2. Client and server
	1. host a server like running program that accepts requests via networking
	2. Access via browser website
	3. Differentiated front and backend
3. Python packages as the desired architecture
4. Hostable via Docker
5. Local networking
6. Local models on run on the tower e.g. locallama and later llama.cpp

# Technical Setup
*The tech that I want to run on and how*
1. Dual setup:
	1. Server: Linux Tower PC running pop-os with a 4060ti, 32gb of ram
	2. Developement/Frontent: Mac with 12gb of ram
2. Development environment
	1. On linux we run vscode as the main ide
	2. On mac we run vscode to access the Tower via ssh and run/change code there
	3. On mac we run claude in code mode as the main development interface
3. Development workflow
	1. Both the frontent and backend will be mainly written from the mac via agentic coding. 
	2. Synchronization between machines via git hosted on codeberg
4. Agentic Development
	1. Ideation is done always with Me
	2. Implementation is done independently besides if things are unclear or milestones are reached
	3. All code can only enter the development branch via a PR
5. Versioning
	1. Git as the versioning system
	2. Remote on codeberg (github alternative, other url pretty much the same interface)
	3. Human readable concise changelog
6. Testing
	1. Unit tests
	2. Test driven design
	3. End to End pipelines with mocks
7. Code
	1. static code checks
	2. linting
	3. pydantic
8. Postgres
	1. exclusively stored and hosted on tower
	2. add ons for vector storage and hybrid retrieval
9. Docker
	1. Both the backend, frontend and database are applications that can be run via Docker
10. Logging
	1. Proper logging using build in functions
	2. Make use of different severities/levels
