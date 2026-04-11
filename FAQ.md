# Frequently Asked Questions (FAQ)

Below are some frequently asked questions about N184.

## How do I get started?

Check out the README.md.  It has quick start instructions.

Essentially you clone our repository, ensure you have dependencies like Python, Podman, and Claude, edit the .env file using .env.example as a template to include your API tokens, and then let it rip with ```./init.sh```.  Honoré does the rest.

## What is N184?

N184 is an open-source code review and vulnerability discovery framework that uses multiple models and validation strategies to surface likely bugs and security issues.

## So it's a magic wand for hackers?

**No.**  The various techniques we use to identify bugs still require a knowledgeable Human in the Loop.  After generating a series of bug reports, someone with sufficient technical expertise still needs to confirm findings and craft the PR and submission.  As an example, if you don't know any C++ you are unlikely to be able to do anything useful with a bug report on a C++ Codebase.

We also are looking for all bugs not just security vulnerabilities.  We imagine a world where software is by default stable, and not as the exception.

## Can I google things in the bug report and figure it out?

Learning from bug reports is part of becoming a better engineer or security researcher, and reading code is a great way to learn how to code. But N184 still assumes technical judgment, responsible use, and manual validation before any finding is acted on.

## Will all N184 instances find the same bugs?

N184, like many LLM based software applications, will learn from its experiences, creating a version unique to **you**.  The pattern library it builds and refers to is based on the repositories you send it to review, and the feedback you provide.  Given enough time, we would expect that N184 instances could learn from each other, and one of our big pushes for v. 2.0 is to provide a standardized way for instances to do that.

## Why is this open source?

Because we believe in open source and because everyone deserves secure, bug free software.

## How accurate is N184?

Ensemble voting significantly reduces false positives compared to single-model analysis. In our testing, findings that pass 2/3 consensus threshold have high validation rates when reviewed by experienced security researchers. However, accuracy depends on target codebase maturity, your validation expertise, and your instance's learned patterns. N184 is a force multiplier, not a replacement for human judgment.

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

## What does this cost to run?

Highly variable depending on codebase size, complexity, and how deep the analysis goes. We've seen runs from under $10 to $100+ in API costs for a single audit. Some factors that increase cost: larger codebases, deeper git history analysis, more complex security patterns, ensemble voting across multiple models. Best practice: set API spending limits in your provider accounts and monitor usage during your first few runs to understand your typical costs.

Additionally, you don't have to run everything through Claude Opus.  You can ask Honoré to use lower cost agents for the swarm. 

Your best bet is to start small to get a feel what actual cost will be.

## Can I just point N184 at any codebase?

Technically, yes.  The amount of useful bug reports you receive will of course be proportional to factors like how well written a code base is.  For fun we ran N184 over OpenBSD and in what may be a first for Artificial Intelligence, it had nothing but compliments for the codebase.  To be honest, we never could have predicted the way Honoré would act like a fan boi reviewing the OpenBSD codebase.

## Which LLM providers do I need?

At a Minimum: Anthropic (Claude). 

Out of the box we support Anthropic + OpenAI + DeepSeek for true multi-model consensus. 

Other models may be supported by default in the future or can be supported if you invoke ```claude``` in your N184 base directory and ask it to add new models.

## Can I run this locally or air-gapped?

Out of the box we require an Anthropic subscription for NanoClaw.  *However* there is no reason you couldn't set this up to run against an on premises model, although it might take some work.  If you do, please contribute your changes back to us at github.

That is currently on our to do list and will hopefully be in a future release.  We're big fans of Apple Silicon's unified architecture and are currently working on implementing an [MLX](https://opensource.apple.com/projects/mlx/) backend.

## What's your disclosure policy?

Responsible disclosure: private notification to maintainers, 90-day fix window (or coordinated timeline), public disclosure only after fix or authorization. See SCOREBOARD.md for disclosed findings.

## So you have findings that you haven't (yet) disclosed?

Yes, and it will stay that way until one of the following happens:

1.  The vulnerability has been patched.
2.  Public disclosure is authorized by the owners of the codebase
3.  It is determined that it is not dangerous to disclose.  (i.e. it's a bug not a vulnerability)

## So what's the difference between bugs, vulnerabilities, and false positives?  How do you know which is which?

**Short answer:** Ask the maintainers of the codebase.

**Longer answer:** What philosophical school do you belong to?

It really depends.

We have reported some bugs that look bad to us.  As an example, we found what we thought was a very effective compression bomb, and we were told by the maintainers, quoting almost verbatim, "This is not an issue because users run the service locally and this wouldn't be an attacker but intentional misbehavior from the computer owner."

We have known how to write secure and bug free software for a long time.  

The problem is the priority of the maintainers.

A bank will spend a lot of effort making sure you can't access their vault.

A public repository **wants** you to have access.

Context changes things.

## So what's your false positive rate?

See above answer.  We can't quantify the false positive rate until everyone agrees what a true positive is.

##  Why are the agents named after Balzac characters?

"Vautrin found it but Goriot rejected it" is easier to debug at midnight than "Agent-001 found it but Agent-004 rejected it." Good naming is a design decision. See the La Comédie Agentique blog post for details.

## How do you compare to Glasswing

Oh boy.  After six months of messing around with agentic swarms, and hours after formally announcing N184 to the world, Anthropic announced [Glasswing](https://www.anthropic.com/glasswing), their initiative to help find and secure software.

It's pretty cool.  It also cost $100,000,000

I built this for $300 in API credits, some Nespresso pods, and some lost sleep.

I'm really proud of what I built.  And I don't think you should need that much money for your software to be stable and bug free.  

Glasswing and N184 are extreme examples of *convergent evolution*.  Birds grew feathers from their arms.  Bats grew skin flaps between their fingers.  Both use them to fly.  And one didn't in any way depend on the other.

## How can I contribute?

1. Submit PRs improving agent prompts, adding new validation checks, or addressing open issues.
2. Report false positives you encounter so we can improve filtering
3. Add support for new LLM providers 
4. Improve documentation or create tutorials.
5. Let people know about the cool work we do.
6. Consider contributing monetarily if you can't contribute time.
7. Anything you can imagine that would help the project


## What's coming next?

The main push for v. 1.0 was to organize my verious experiments in agentic bug searching into something portable.  Right now, that means we really just have a wrapper around NanoClaw, providing a set of standard instructions as to what NanoClaw shoudl do.  That's useful, and extremely helpful.  **But there's more**

The roadmap is, roughly:

1. Provide a formal pattern database mechanism.  Right now my N184 agent learns from its own research, and interaction with me.  But ideally anyone running N184 should be able to update to the most recent definition file.
2. Provide access to local LLMs run via [MLX](https://opensource.apple.com/projects/mlx/)
3. Give each agent their own docker container or pod to make it easier to see what's going on.
4. Find more bugs
5. Squash those bugs
6. ???
7. Happiness.


