import os
import streamlit as st
import re 
from langchain_groq import ChatGroq
from langchain_community.tools import ArxivQueryRun
from langchain_community.utilities import ArxivAPIWrapper
from langchain.text_splitter import RecursiveCharacterTextSplitter

from pydantic import BaseModel, Field
from typing import List, Annotated
from typing_extensions import TypedDict
import operator

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.constants import Send
from langgraph.graph import StateGraph, START, END

# === LLM and Tools ===
os.environ["GROQ_API_KEY"] = st.secrets[GROQ_API_KEY ]
llm = ChatGroq(model="qwen-qwq-32b")

api_wrapper_arxiv = ArxivAPIWrapper(top_k_results=5, doc_content_chars_max=1000)
arxiv_tool = ArxivQueryRun(api_wrapper=api_wrapper_arxiv)
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)

# === Section Schema ===
class Section(BaseModel):
    name: str
    description: str

class Sections(BaseModel):
    sections: List[Section]

class State(TypedDict):
    topic: str
    approach: str
    results: str
    sections: List[Section]
    completed_sections: Annotated[list, operator.add]
    arxiv_results: list
    final_report: str

class WorkerState(TypedDict):
    section: Section
    topic: str
    approach: str
    results: str
    arxiv_results: list
    completed_sections: Annotated[list, operator.add]

RESEARCH_SECTIONS = [
    {"name": "Abstract", "description": "Write a concise academic abstract for the research paper."},
    {"name": "Introduction", "description": "Introduce the problem, explain importance, and state objectives."},
    {"name": "Literature Review", "description": "Summarize and analyze related papers from arxiv on the topic."},
    {"name": "Methodology", "description": "Describe the research methods and approach clearly."},
    {"name": "Results", "description": "Present the research findings and observations."},
    {"name": "Conclusion", "description": "Summarize key findings and contributions."},
    {"name": "Future Scope", "description": "Suggest directions for future work."},
    {"name": "References", "description": "List all cited papers in proper format."},
]

# === Nodes ===
def orchestrator(state: State):
    sections = [Section(**sec) for sec in RESEARCH_SECTIONS]
    return {"sections": sections, "completed_sections": [], "arxiv_results": []}

def arxiv_lit_review(state: WorkerState):
    topic = state["topic"]
    raw_results = arxiv_tool.invoke(topic) or []
    arxiv_papers, references_text = [], []

    for doc in raw_results:
        meta = getattr(doc, "metadata", {})
        title = meta.get("title", "Unknown Title")
        authors = ", ".join(meta.get("authors", [])) or "Unknown Authors"
        summary_text = getattr(doc, "page_content", "")

        arxiv_papers.append({"title": title, "authors": authors, "summary": summary_text, "raw_doc": doc})
        references_text.append(f"{authors}. \"{title}\". arXiv preprint.")

    state["arxiv_results"] = arxiv_papers

    chunks = [chunk for paper in arxiv_papers for chunk in text_splitter.split_text(paper["summary"])]
    lit_summary_parts = [llm.invoke([
        SystemMessage(content="You are a helpful summarizer of academic texts."),
        HumanMessage(content=f"Summarize this text concisely:\n\n{chunk}")
    ]).content for chunk in chunks]

    lit_summary_text = "\n".join(lit_summary_parts)
    lit_review_section = llm.invoke([
        SystemMessage(content="You are an academic writer drafting a literature review section."),
        HumanMessage(content=f"Based on the following summarized academic papers:\n\n{lit_summary_text}\n\nWrite a coherent Literature Review section. Mention authors and key findings, and identify gaps , display comparison tables between techniques , and write in paragraphs , dont give conclusion.")
    ])
    return {
        "completed_sections": [f"Literature Review\n\n{lit_review_section.content}"],
        "arxiv_results": arxiv_papers
    }

def llm_call(state: WorkerState):
    section = state["section"]
    section_name, section_desc = section.name, section.description

    if section_name == "Literature Review":
        return arxiv_lit_review(state)

    if section_name == "References":
        refs = [
            f"- {paper['authors']}. \"{paper['title']}\". arXiv preprint."
            for paper in state.get("arxiv_results", [])
        ]
        return {"completed_sections": [f"References\n\n" + "\n".join(refs)]}

    prompt_map = {
    "Abstract": f"Topic: {state['topic']}\nApproach: {state['approach']}\nResults: {state['results']} (in 150 words , add 4 keywords)",
    "Introduction": f"Explain the importance of the topic: {state['topic']} and objective using approach: {state['approach']} (in paragraphs, not points. Do not add a conclusion).",
    "Methodology": f"Describe methodology for the approach: {state['approach']} (in detailed paragraph-based format, not points. Do not add a conclusion , show flow diagram if required).",
    "Results": f"Explain research results: {state['results']} (add metrics tables for evaluation).",
    "Conclusion": f"Summarize findings for topic: {state['topic']} (do not add future directions, just a precise conclusion paragraph).",
    "Future Scope": f"Suggest 3 future directions only for: {state['topic']}.",
    }


    prompt = prompt_map.get(section_name, f"Write the {section_name} section for topic: {state['topic']}.")
    llm_response = llm.invoke([
        SystemMessage(content=f"Write a section titled '{section_name}'. {section_desc}"),
        HumanMessage(content=prompt)
    ])
    return {"completed_sections": [f" {section_name}\n\n{llm_response.content}"]}

def synthesizer(state: State):
    final_report = "\n\n---\n\n".join(state["completed_sections"])
    return {"final_report": final_report}

def assign_workers(state: State):
    return [
        Send("llm_call", {
            "section": s, "topic": state["topic"], "approach": state["approach"],
            "results": state["results"], "arxiv_results": state.get("arxiv_results", [])
        })
        for s in state["sections"]
    ]

# === LangGraph ===
graph = StateGraph(State)
graph.add_node("orchestrator", orchestrator)
graph.add_node("llm_call", llm_call)
graph.add_node("synthesizer", synthesizer)
graph.add_edge(START, "orchestrator")
graph.add_conditional_edges("orchestrator", assign_workers, ["llm_call"])
graph.add_edge("llm_call", "synthesizer")
graph.add_edge("synthesizer", END)
workflow = graph.compile()

def generate_research_paper(topic, approach, results):
    state = {"topic": topic, "approach": approach, "results": results}
    result = workflow.invoke(state)
    return result["final_report"]

# === Streamlit UI ===
def main():
    st.title("🧠 AI Research Paper Generator")
    st.write("Generate a full research paper using LangGraph and arXiv.")

    topic = st.text_input("Enter Research Topic", "Agentic AI and RAG")
    approach = st.text_area("What was the Approach you used ", "We use LangChain and LangGraph to build agentic RAG pipelines.")
    results = st.text_area("What were your Results", "The system works effectively in generating sectioned research outputs.")

    if st.button("Generate Research Paper"):
        with st.spinner("Generating paper... This may take a minute."):
            try:
                paper_md_raw = generate_research_paper(topic, approach, results)
    
                # Remove all <think>...</think> tags using regex
                paper_md = re.sub(r"<think>.*?</think>", "", paper_md_raw, flags=re.DOTALL).strip()
    
                st.success("Research paper generated successfully!")
                st.write(paper_md)
    
            except Exception as e:
                st.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
