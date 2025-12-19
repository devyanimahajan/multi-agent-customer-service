# multi-agent-customer-service
Build a multi-agent customer service system where specialized agents coordinate using Agent-to-Agent (A2A) communication and access customer data through the Model Context Protocol (MCP).

# Multi-Agent Customer Service System (A2A + MCP)

## Overview
This project implements a multi-agent customer service system using:
- Agent-to-Agent (A2A) communication for coordination
- Model Context Protocol (MCP) over stdio for database access

## Agents
- Router Agent: intent analysis, task planning, orchestration
- Customer Data Agent: customer and ticket data via MCP
- Support Agent: customer-facing responses and escalation

## How to Run
1. Install dependencies  
   pip install -r requirements.txt

2. Create database  
   python database_setup.py

3. Start agents (in separate terminals or via notebook)

4. Run demo  
   python run_demo.py

## Scenarios Demonstrated
- Task allocation
- Negotiation and escalation
- Multi-step coordination
