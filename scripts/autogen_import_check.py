"""
AutoGen import smoke test.

This confirms the local environment can load Microsoft AutoGen AgentChat
without running any clinical workflow or calling any model.
"""

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import TextMentionTermination
from autogen_ext.models.openai import OpenAIChatCompletionClient, AzureOpenAIChatCompletionClient


def main():
    print("AutoGen import check passed.")
    print("Loaded:")
    print("- AssistantAgent")
    print("- RoundRobinGroupChat")
    print("- TextMentionTermination")
    print("- OpenAIChatCompletionClient")
    print("- AzureOpenAIChatCompletionClient")


if __name__ == "__main__":
    
    main()