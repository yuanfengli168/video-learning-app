# Compare-AI-Results — Run Scorecard

**Run:** 2026-09-23T015607 · **Videos:** 50 · **Prompt:** 65d4753dd576efa2 (bundled)

## Headline (per model)

| Metric | glm-5.2:cloud | minimax-m3:cloud | glm-5.3-flash:cloud | deepseek-v4.1-flash:cloud |
|---|---|---|---|---|
| Videos scored | 50 | 50 | 50 | 50 |
| Parse OK (hard gate) | 100% | 98% | 100% | 100% |
| Thinking contamination | 0% | 0% | 0% | 0% |
| Structure clean (hard gate) | 100% | 96% | 100% | 100% |
| Latency median (s) | 35.599999999999994 | 14.75 | 62.7 | 31.1 |

## Topic↔Mindmap match (the minimax question)

| Metric | glm-5.2:cloud | minimax-m3:cloud | glm-5.3-flash:cloud | deepseek-v4.1-flash:cloud |
|---|---|---|---|---|
| Topic entries (total) | 577 | 871 | 643 | 665 |
| EXACT match (production rule) | 575 | 751 | 643 | 664 |
| Exact rate | 100% | 86% | 100% | 100% |
| NORMALIZED match (candidate fix) | 575 | 803 | 643 | 664 |
| Normalized rate = fix margin | 100% | 92% | 100% | 100% |

> **Read:** Exact rate = today's clickability. Normalized rate
> = what a click-time normalizer (case-fold + substring)
> would recover. The gap between them is the fix margin.

## Grounding (transcript word overlap)

| Metric | glm-5.2:cloud | minimax-m3:cloud | glm-5.3-flash:cloud | deepseek-v4.1-flash:cloud |
|---|---|---|---|---|
| Median node overlap | 1.0 | 1.0 | 1.0 | 1.0 |
| Ungrounded nodes (total) | 88 | 182 | 66 | 82 |

## Content metrics (style — no pass/fail)

| Metric | glm-5.2:cloud | minimax-m3:cloud | glm-5.3-flash:cloud | deepseek-v4.1-flash:cloud |
|---|---|---|---|---|
| Flashcards median | 9.5 | 10 | 10.0 | 10.0 |
| Quiz median | 5.0 | 5 | 5.0 | 5.0 |
| Topics median | 10.5 | 16 | 14.5 | 14.0 |
| Mindmap nodes median | 20.0 | 44 | 29.0 | 28.5 |
| Summary chars median | 1944.5 | 2569 | 2780.0 | 2294.0 |

## Mismatch log (exact-match failures, all models)

| Model | Video | Topic (as generated) | Normalized fixes? | Closest mindmap node |
|---|---|---|---|---|
| glm-5.2:cloud | Ollama Switched to Apple MLX - Here's Wh | 'Summary & Conclusion' | ❌ | '—' |
| glm-5.2:cloud | 1: Introduction to Neural Networks and D | 'Feature Engineering / Representation' | ❌ | '—' |
| minimax-m3:cloud | Your first Claude Code prompt | 'Toggle with Shift+Tab' | ❌ | '—' |
| minimax-m3:cloud | Your first Claude Code prompt | 'How It Works' | ❌ | '—' |
| minimax-m3:cloud | Your first Claude Code prompt | 'Best Use Cases' | ❌ | '—' |
| minimax-m3:cloud | Your first Claude Code prompt | 'Plan Mode in Action' | ✅ | 'Plan Mode' |
| minimax-m3:cloud | The Explore → Plan → Code → Commit workf | 'Plan Mode' | ✅ | 'Use plan mode (Shift+Tab)' |
| minimax-m3:cloud | The Explore → Plan → Code → Commit workf | 'Explore without plan mode' | ✅ | 'Explore' |
| minimax-m3:cloud | Context Management in Claude Code | 'Automatic Compaction' | ✅ | '/compact' |
| minimax-m3:cloud | Context Management in Claude Code | '/compact Command' | ✅ | '/compact' |
| minimax-m3:cloud | Context Management in Claude Code | '/clear Command' | ✅ | '/clear' |
| minimax-m3:cloud | Context Management in Claude Code | '/context Command' | ✅ | 'Context Commands' |
| minimax-m3:cloud | MCP in Claude Code | 'Context Window Impact' | ❌ | '—' |
| minimax-m3:cloud | Hooks in Claude Code | 'Deterministic vs Probabilistic' | ❌ | '—' |
| minimax-m3:cloud | Hooks in Claude Code | 'Exit Codes for PreToolUse Hooks' | ✅ | 'PreToolUse' |
| minimax-m3:cloud | Hooks in Claude Code | 'Use Cases for Blocking Hooks' | ❌ | '—' |
| minimax-m3:cloud | Hooks in Claude Code | 'CLAUDE_PROJECT_DIR Environment Variable' | ❌ | '—' |
| minimax-m3:cloud | Lesson 1: Introduction to AI Fluency | A | 'From Thinking About AI to Thinking With AI' | ❌ | '—' |
| minimax-m3:cloud | Lesson 1: Introduction to AI Fluency | A | 'Widening Gap Between Possibilities and Comfort' | ❌ | '—' |
| minimax-m3:cloud | Lesson 1: Introduction to AI Fluency | A | 'Delegation' | ❌ | '—' |
| minimax-m3:cloud | Lesson 1: Introduction to AI Fluency | A | 'Description' | ❌ | '—' |
| minimax-m3:cloud | Lesson 1: Introduction to AI Fluency | A | 'Discernment' | ❌ | '—' |
| minimax-m3:cloud | Lesson 1: Introduction to AI Fluency | A | 'Diligence' | ❌ | '—' |
| minimax-m3:cloud | Lesson 2A: Why do we need AI Fluency? |  | 'Director vs. Scriptwriter Mindset' | ❌ | '—' |
| minimax-m3:cloud | Lesson 4: A closer look at Delegation |  | 'Cornerstone of Good Delegation' | ❌ | '—' |
| minimax-m3:cloud | Lesson 4: A closer look at Delegation |  | 'Types of Work Areas' | ❌ | '—' |
| minimax-m3:cloud | Lesson 4: A closer look at Delegation |  | 'Hands-On Experimentation' | ✅ | 'Hands-on experimentation' |
| minimax-m3:cloud | Lesson 6: A closer look at Description | | 'Bridge Metaphor' | ❌ | '—' |
| minimax-m3:cloud | Lesson 6: A closer look at Description | | 'AI as Interactive System' | ❌ | '—' |
| minimax-m3:cloud | Lesson 11: Conclusion | AI Fluency: Fram | 'AI as Thinking Partner' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'A20 Pro Chip' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone 18 Pro Battery' | ✅ | 'Battery' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'Pro controls' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'Apple Reference Image' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iOS 27 Features' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone 18 Pro Pricing & Availability' | ✅ | 'Pricing & Availability' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'Readiness/Health App' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'Apple Watch Design' | ✅ | 'Design' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone Duo Design' | ✅ | 'Design' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone Duo Software (iOS 27)' | ✅ | 'Software (iOS 27)' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone Duo Hardware' | ❌ | '—' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone Duo Camera' | ✅ | 'Camera' |
| minimax-m3:cloud | Apple Event September 9 2026: Introducin | 'iPhone Duo Pricing & Availability' | ✅ | 'Pricing & Availability' |
| minimax-m3:cloud | NVIDIA just announced the ULTIMATE deskt | 'NVIDIA DGX Spark & DGX Station' | ✅ | 'DGX Station' |
| minimax-m3:cloud | NVIDIA just announced the ULTIMATE deskt | 'VRAM Limit (32GB)' | ❌ | '—' |
| minimax-m3:cloud | NVIDIA just announced the ULTIMATE deskt | 'FP4 (Floating Point Four)' | ✅ | 'FP4 Floating Point Four' |
| minimax-m3:cloud | 1: Introduction to Neural Networks and D | "Polanyi's Paradox" | ❌ | '—' |
| minimax-m3:cloud | 1: Introduction to Neural Networks and D | 'Three Forces Behind Deep Learning' | ✅ | 'Deep Learning' |
| minimax-m3:cloud | 1: Introduction to Neural Networks and D | 'Deep Learning Applications' | ✅ | 'Deep Learning' |
| minimax-m3:cloud | 1: Introduction to Neural Networks and D | 'Logistic Regression as a Network' | ❌ | '—' |
| minimax-m3:cloud | 1: Introduction to Neural Networks and D | 'Weights and Biases' | ❌ | '—' |
| minimax-m3:cloud | 1: Introduction to Neural Networks and D | 'Feedforward Neural Network' | ✅ | 'Neural Networks' |
| minimax-m3:cloud | 2: Training Deep NNs (cont.); Introducti | 'Update Formula: w = w - alpha * gradient' | ✅ | 'Update Formula: w = w - alpha  gradient' |
| minimax-m3:cloud | 2: Training Deep NNs (cont.); Introducti | 'Parameter Explosion' | ❌ | '—' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Training Checklist' | ✅ | 'Training' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Model Training' | ✅ | 'Training' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Model Evaluation' | ❌ | '—' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Training Output Interpretation' | ✅ | 'Training' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Loss and Accuracy Curves' | ❌ | '—' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Multi-Class Loss Functions' | ✅ | 'Loss Function' |
| minimax-m3:cloud | 3: Deep Learning for Computer Vision – B | 'Fashion-MNIST Colab' | ❌ | '—' |
| minimax-m3:cloud | 4: Deep Learning for Computer Vision – T | 'ResNet Implementation & Demo' | ❌ | '—' |
| minimax-m3:cloud | 5: Deep Learning for Natural Language –  | 'High-level deep learning view' | ❌ | '—' |
| minimax-m3:cloud | 5: Deep Learning for Natural Language –  | 'Vocabulary Creation and Indexing' | ✅ | 'Indexing' |
| minimax-m3:cloud | 5: Deep Learning for Natural Language –  | 'Indexing & One-Hot Encoding' | ✅ | 'Indexing' |
| minimax-m3:cloud | 5: Deep Learning for Natural Language –  | 'Song Genre Classification Setup' | ✅ | 'Song Genre Classification' |
| minimax-m3:cloud | 5: Deep Learning for Natural Language –  | 'Neural Network Architecture for NLP' | ✅ | 'Architecture' |
| minimax-m3:cloud | 5: Deep Learning for Natural Language –  | 'Bigrams (N-grams)' | ✅ | 'N-grams' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'From One-Hot Vectors to Word Embeddings' | ✅ | 'Word Embeddings' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Context and Polysemy' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | "Firth's Insight and Co-occurrence Counting" | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Evaluating Embeddings Quality' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'GloVe Mathematical Formulation' | ✅ | 'Mathematical Formulation' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Capturing Context Through Co-occurrence' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Gradient Descent for Training' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Embedding Dimensionality' | ✅ | 'Embedding dimensionality' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Word Vector Algebra' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Bias and Practical Considerations' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Embedding Layer Setup' | ✅ | 'Embedding Layer' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Pre-trained vs Trainable Embeddings' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Training from Scratch' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Model Performance Comparison' | ❌ | '—' |
| minimax-m3:cloud | 6: Deep Learning for Natural Language –  | 'Final Results and Trade-offs' | ❌ | '—' |
| minimax-m3:cloud | 10: Generative AI – Adapting LLMs with P | 'RAG Implementation Details' | ❌ | '—' |
| minimax-m3:cloud | seoul guide to shopping, eating and coff | 'Map and Check-in' | ❌ | '—' |
| minimax-m3:cloud | seoul guide to shopping, eating and coff | 'HAUS NOWHERE' | ❌ | '—' |
| minimax-m3:cloud | seoul guide to shopping, eating and coff | 'Korean BBQ at Ggupdang' | ❌ | '—' |
| minimax-m3:cloud | Lee Kuan Yew on Leadership: The Harvard  | 'Building Institutions' | ❌ | '—' |
| minimax-m3:cloud | Lee Kuan Yew on Leadership: The Harvard  | 'Talent Selection' | ❌ | '—' |
| minimax-m3:cloud | Apple Event — September 9, 2025 | 'Satellite Connectivity' | ✅ | 'Satellite connectivity' |
| minimax-m3:cloud | Apple Event — September 9, 2025 | 'A19 Chip (3nm)' | ✅ | 'A19 chip (3nm)' |
| minimax-m3:cloud | Apple Event — September 9, 2025 | 'Center Stage Front Camera' | ✅ | 'Center Stage front camera' |
| minimax-m3:cloud | Apple Event — September 9, 2025 | 'A19 Pro Chip' | ✅ | 'A19 Pro chip' |
| minimax-m3:cloud | Apple Event — September 9, 2025 | 'Vapor Chamber Thermal System' | ✅ | 'Vapor chamber thermal system' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Supervised Learning' | ✅ | 'Unsupervised Learning' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Levels of Abstraction' | ❌ | '—' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Deep Learning in Computer Vision' | ❌ | '—' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Recurrent Neural Network' | ❌ | '—' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Data Collection Challenges' | ❌ | '—' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Computational Scale' | ❌ | '—' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Deep Speech Architecture' | ✅ | 'Architecture' |
| minimax-m3:cloud | Stanford Seminar - Deep Speech: Scaling  | 'Scaling' | ❌ | '—' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Mel Scale and MFCC Features' | ✅ | 'Mel Scale' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Phonetic Units and Phonology' | ✅ | 'Phonetic Units' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Context-Dependent Phonetic Units' | ✅ | 'Phonetic Units' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Fundamental Equation of Speech Recognition' | ❌ | '—' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Gaussian Mixture Models for Acoustic Modeling' | ✅ | 'Acoustic Modeling' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Forced Alignment and Decoding' | ✅ | 'Forced Alignment' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Neural Networks in Speech Recognition' | ❌ | '—' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Hybrid Neural Network-HMM Systems' | ❌ | '—' |
| minimax-m3:cloud | Lecture 9 - Speech Recognition (ASR) [An | 'Sequence-to-Sequence and Attention Models' | ✅ | 'Attention Models' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Introduction and Overview' | ✅ | 'Overview' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Limited Weight Sharing' | ❌ | '—' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Multiple Pooling Sizes' | ❌ | '—' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Softmax Pooling' | ❌ | '—' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Pre-training' | ❌ | '—' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Time Domain Convolution' | ❌ | '—' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Segmental model overview' | ✅ | 'Overview' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Segment Score Computation Methods' | ❌ | '—' |
| minimax-m3:cloud | Deep and segmental convolutional neural  | 'Summary and Future Work' | ✅ | 'Future Work' |
| minimax-m3:cloud | Speech Emotion Recognition with Convolut | 'Initial Experiments' | ❌ | '—' |
| minimax-m3:cloud | Lecture 12: End-to-End Models for Speech | 'CTC Results and Word Error Rate' | ✅ | 'Results and Word Error Rate' |
| deepseek-v4.1-flash:cloud | Hooks in Claude Code | 'CLAUDE_PROJECT_DIR variable' | ❌ | '—' |

### Mismatch resolution summary

- **glm-5.2:cloud**: 2 mismatch(es) — normalizer resolves 0 (0%); 2 remain unresolved
- **minimax-m3:cloud**: 120 mismatch(es) — normalizer resolves 52 (43%); 68 remain unresolved
- **glm-5.3-flash:cloud**: zero exact-match failures 🎉
- **deepseek-v4.1-flash:cloud**: 1 mismatch(es) — normalizer resolves 0 (0%); 1 remain unresolved
