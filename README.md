# just-chat-chemistry

> 🧪 This is a **chemistry-focused fork** of [just-chat](https://github.com/longevity-genie/just-chat) with domain-specific tools to work with molecular structures, names, and chemical properties using SMILES and PubChem.

---

## 🚀 Quick Start

Just clone the repository and run using Docker or Podman:

```bash
git clone https://github.com/your-org/just-chat-chem.git
cd just-chat-chem
USER_ID=$(id -u) GROUP_ID=$(id -g) docker-compose up
```

Then open your browser to `http://localhost:3000` to start chatting with your **chemistry agent**!

---

## 🧪 What’s Special About This Fork?

This version includes a **specialized chemistry agent** with tools that allow:

- 🔁 **SMILES ↔ IUPAC Name Conversion** using PubChem  
- 🧬 **Functional Group Identification** from SMILES or names  
- 📊 **Retrieving Physical Properties** (e.g. molecular weight, boiling point) from PubChem  
- 🧠 Support for **chemistry-focused reasoning** with modern LLMs (e.g. LLaMA 3.3, DeepSeek, GPT, etc.)

Chemistry tools are located in `/agent_tools/chemistry_tools/`, extending the base functionality of `just-chat`.

---

## 🧑‍🔬 Customize Your Agent

1. Edit `chat_agent_profiles.yaml` to configure the chemistry agent’s personality and model.  
2. Modify or extend chemistry tools in `agent_tools/chemistry_tools/`.  
3. Add additional models, prompts, or custom logic as needed.

---

## 🔬 Chemistry Tools Included

| Tool Name                | Description                                                                 |
|--------------------------|-----------------------------------------------------------------------------|
| `smiles_to_name`         | Converts SMILES strings to IUPAC names using PubChem                        |
| `name_to_smiles`         | Converts IUPAC/common names to SMILES using PubChem                         |
| `functional_groups`      | Identifies functional groups from SMILES or names                           |
| `chemical_properties`    | Fetches physical and chemical properties (e.g. mol weight, BP, logP, etc.)  |
| `similarity_search_3d`   | Performs 3D similarity search using SMILES and returns similar compounds    |

Each tool uses public APIs like **PubChem** to ensure accurate and reliable chemistry data.

---

## 💬 Interface & Usage

- ✅ Access the web‑based chat UI at `http://localhost:3000`  
- 🧪 Talk to your chemistry agent using natural language, e.g.:

```text
“What are the functional groups in caffeine?”
“Convert this SMILES string to a name: CN1C=NC2=C1C(=O)N(C(=O)N2C)”
“Get physical properties for benzene”
```

---

## ⚙️ Features

- 🚀 One-command startup (`docker compose up` or `podman-compose up`)  
- 🧠 Agent configuration via YAML  
- 🛠️ Extend functionality with custom tools (Python)  
- 🌐 Web interface for interactive chat  
- 📦 Docker/Podman support — no local Python/Node needed  
- 🔑 API key integration for LLMs (Groq, OpenAI, etc.)  
- 📚 Semantic document search via MeiliSearch (optional)

---

## 💡 Example Prompts to Try

| Prompt Example                                               | Description                    |
|--------------------------------------------------------------|--------------------------------|
| “What is the IUPAC name of `CC(=O)OC1=CC=CC=C1C(=O)O`?”      | Converts SMILES to name        |
| “Give me the SMILES of aspirin”                              | Name → SMILES                  |
| “What functional groups are in morphine?”                    | Parses and identifies groups   |
| “What is the molecular weight and boiling point of acetone?” | Retrieves properties           |

---

## 🔐 API Key Configuration

Edit the `env/.env.keys` file with your preferred provider(s):

```bash
GROQ_API_KEY=your_groq_key
OPENAI_API_KEY=your_openai_key
MISTRAL_API_KEY=your_mistral_key  # optional
```

Then restart the container:

```bash
docker compose down
docker compose up
```

or

```bash
podman-compose down
podman-compose up
```

---

## 🧠 LLM Support

This project uses [just-agents](https://github.com/longevity-genie/just-agents) to enable nearly any modern LLM via YAML config:

- GPT‑4 / GPT‑3.5  
- Claude  
- LLaMA 3.3  
- DeepSeek  
- Mistral  
- Groq LLMs  
- Local models via Ollama (optional)

---

## 🔍 Semantic Document Search (Optional)

1. Drop `.md` or `.pdf` chemistry files into `data/chem_docs`  
2. Start the service and visit `http://localhost:9000/docs` (FastAPI Swagger)  
3. Use `/index_markdown` or `/index_pdf` to add to MeiliSearch  
4. Enable semantic search in `chat_agent_profiles.yaml`  

Then your chemistry agent can answer questions based on your lab manuals, papers, or notes.

---

## 🤝 Credits & Acknowledgments

This project is built on top of:

- 🔗 [just-chat](https://github.com/longevity-genie/just-chat) by Longevity Genie  
- 🧠 [just-agents](https://github.com/longevity-genie/just-agents) for LLM orchestration

Special thanks to:

[![HEALES](images/heales.jpg)](https://heales.org/)  
[![IBIMA](images/IBIMA.jpg)](https://ibima.med.uni-rostock.de/)

---

## 🧪 Contributing Chemistry Tools

Want to add your own chemistry tool?

1. Create a Python file in `agent_tools/chemistry_tools/`  
2. Define a `run()` function  
3. Add a `tool_definition` dict with `name`, `description`, and `inputs`  
4. Rebuild container with `docker-compose down && docker-compose up`

Example skeleton:

```python
tool_definition = {
    "name": "my_tool",
    "description": "Describe what your tool does",
    "parameters": {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "Your input"},
        },
        "required": ["input"],
    },
}

def run(input: str):
    return f"You entered: {input}"
```

---

## 🧼 Tips

- ✅ Pull regularly to keep containers up to date  
- 🧹 Use `docker compose down` or `podman-compose down` before switching config  
- 🗃️ Logs are in `logs/` even after shutdown  
- 💬 Chat history stored in persistent MongoDB

---

## 🧪 Happy Moleculing!

Your personal chemistry assistant is now live. Use it to:

- Explore molecule properties  
- Convert chemical names  
- Analyze functional groups  
- Reference your own chemistry docs

Ready to experiment? Just chat 🔬
