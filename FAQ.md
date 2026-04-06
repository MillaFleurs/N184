# Frequently Asked Questions (FAQ)

Below are some frequently asked questions about N184.

## How do I get started?

Check out the README.md.  It has quick start instructions.

## What is N184?

N184 is an open-source code review and vulnerability discovery framework that uses multiple models and validation strategies to surface likely bugs and security issues.

## So it's a magic wand for hackers?

**No.**  The various techniques we use to identify bugs still require a knowledgable Human in the Loop.  After generating a series of bug reports, someone with sufficient technical expertise still needs to confirm findings and craft the PR and submission.  As an example, if you don't know any C++ you are unlikely to be able to do anything useful with a bug report on a C++ Codebase.

## But couldn't I google things in the bug report and figure it out?

Learning from bug reports is part of becoming a better engineer or security researcher, and reading code is a great way to learn how to code. But N184 still assumes technical judgment, responsible use, and manual validation before any finding is acted on.

## Will all N184 instances find the same bugs?

N184, like many LLM based software applications, will learn from its experiences, creating a version unique to **you**.  The pattern library it builds and refers to is based on the repositories you send it to review, and the feedback you provide.  Given enough time, we would expect that N184 instances could learn from each other, and one of our big pushes for v. 2.0 is to provide a standardized way for instances to do that.

## Why is this open source?

Because we believe in open source and because everyone deserves secure, bug free software.

## Why ensemble methods? Why not just use Claude Opus or GPT-5?

Single models hallucinate. They flag sudo as a vulnerability and panic about documented features. Ensemble consensus (2/3 voting threshold) filters false positives while preserving real findings. The math is proven in spam filters and credit scoring.

Put another way, ensemble methods also allow you to tailor things a number of ways.  Why not have a Python specific model look at python code?  Why not have a Pen Testing model handle the world facing interface?

## How do you support so many models?

We chose the NanoClaw architecture for this reason.  At the end of the day, if you want to do something that is not supported out of the box, go to your NanoClaw directory, and invoke ```claude```.  You can tell Claude Code exactly what changes you need to make.  Just be sure to also ask it to package it and submit as a PR if it's something cool so we can all take advantage of your genius.

## How do you achieve model independence? Don't all LLMs make similar mistakes?

Three ways: 

1. Architectural diversity (DeepSeek MoE vs Claude/GPT dense transformers) 
2. Git archaeology provides non-LLM signal
3. Adversarial validation (Devil's Advocate explicitly challenges consensus). 

## What size codebases can N184 handle?

We've successfully audited targets from 50K lines (MLX components) to 2M+ lines (GitLab CE). Larger targets consume more API budget and time. 

## Can I just point N184 at any codebase?

Technically yes.  The amount of useful bug reports you receive will of course be proportional to factors like how well written a code base is.  For fun we ran N184 over OpenBSD and in what may be a first for Artificial Intelligence, it had nothing but compliments for the codebase.  To be honest, we never could have predicted the way Honoré would act like a fan boi reviewing the OpenBSD codebase.

## Which LLM providers do I need?

At a Minimum: Anthropic (Claude). 

Out of the box we support Anthropic + OpenAI + DeepSeek for true multi-model consensus. 

Other models may be supported by default in the future or can be supported if you invoke ```claude``` in your N184 base directory and ask it to add new models.

## What does this cost to run?

Depends on a number of factors including size of code base, complexity, and models run.  Your best bet is to monitor usage.

## Can I run this locally or air-gapped?

Out of the box we require an Anthropic subscription for NanoClaw.  *However* there is no reason you couldn't set this up to run against an on premises model, although it might take some work.  If you do, please contribute your changes back to us at github.

## What's your disclosure policy?

Responsible disclosure: private notification to maintainers, 90-day fix window (or coordinated timeline), public disclosure only after fix or authorization. See SCOREBOARD.md for disclosed findings.

## So you have findings that you haven't (yet) disclosed?

Yes, and it will stay that way until it has been determined that the vulnerability has been patched or public disclosure has been authorized.

## How can I contribute?

1. Submit PRs improving agent prompts, adding new validation checks, or addressing open issues.
2. Report false positives you encounter so we can improve filtering
3. Add support for new LLM providers 
4. Improve documentation or create tutorials.
5. Let people know about the cool work we do.
6. Consider contributing monetarily if you can't contribute time.
7. Anything you can imagine that would help the project

##  Why are the agents named after Balzac characters?

"Vautrin found it but Goriot rejected it" is easier to debug at midnight than "Agent-001 found it but Agent-004 rejected it." Good naming is a design decision. See the La Comédie Agentique blog post for details.
