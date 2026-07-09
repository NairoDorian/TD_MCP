# TouchDesigner — Documentation & Useful Links

> A curated, living collection of TouchDesigner (TD) resources: official docs, Python class references, GitHub repos, learning courses, community forums, and third-party tools.
> Last brainstorm pass: expanded Python modules, core concepts, key operators, debugging tools, and integrations.

---

## 1. Official Documentation & Reference

| Link | Description |
|------|-------------|
| [docs.derivative.ca](https://docs.derivative.ca/) | The official TouchDesigner Wiki — the central reference for every operator, parameter, and concept. Start here. |
| [Operator Categories / Families](https://docs.derivative.ca/Operator_Family) | Explains the 6 operator families: TOP, CHOP, SOP, POP, DAT, COMP. |
| [Learning About POPs](https://docs.derivative.ca/Learning_About_POPs) | Deep guide to Point Operators (POPs) — GPU-based geometry/point/particle system, the modern successor to SOPs. |
| [OP Snippets](https://docs.derivative.ca/OP_Snippets) | Built-in example browser for every operator (`Help → Operator Snippets`). Best way to learn a node fast. |
| [Operator Index](https://docs.derivative.ca/Category:Operators) | Full alphabetical list of all operators. |
| [Reference Category](https://docs.derivative.ca/Category:Reference) | Index of all reference pages by category. |
| [Palette](https://docs.derivative.ca/Palette) | Pre-built tools/components shipped with TD (`Dialogs → Palette`). |
| [Glossary](https://docs.derivative.ca/TouchDesigner_Glossary) | Definitions of TD terminology. |
| [Keyboard Shortcuts](https://docs.derivative.ca/Keyboard_Shortcuts) | Full list of hotkeys. |
| [Experimental Builds](https://docs.derivative.ca/Experimental_Builds) | Experimental vs official builds and release notes. |
| [Optimization](https://docs.derivative.ca/Optimization) | Performance best-practices wiki. |
| [File Types (.toe/.tox)](https://docs.derivative.ca/TouchDesigner_File_Types) | Project vs component file formats. |

---

## 2. Python: Modules, Classes & Core Concepts

### 2a. Modules
| Link | Description |
|------|-------------|
| [Python Reference Index](https://docs.derivative.ca/Category:Python_Reference) | Index of all Python classes and modules in TD. |
| [`td` Module](https://docs.derivative.ca/Td_Module) | Core module — `op()`, `parent()`, `me`, `ext` live here. |
| [`tdu` Module](https://docs.derivative.ca/Tdu_Module) | TouchDesigner Utility module — `tdu.rand()`, `tdu.clamp()`, `tdu.hexToRGB()`, file/path helpers. **Use constantly.** |
| [`TDFunctions`](https://docs.derivative.ca/TDFunctions) | Helper functions for cloning, parameters, storage, etc. |
| [Dependency Class](https://docs.derivative.ca/Dependency_Class) | `tdu.Dependency` — cook-on-change binding for Python variables. |

### 2b. Operator / Data Classes
| Link | Description |
|------|-------------|
| [OP Class](https://docs.derivative.ca/OP_Class) | Base class for every operator (`.par`, `.digits`, `.name`, `.parent()`, `.store`/`fetch`). |
| [COMP Class](https://docs.derivative.ca/COMP_Class) | Component operators (networks, Geometry, Container, Replicator…). |
| [TOP Class](https://docs.derivative.ca/TOP_Class) | Texture operators. |
| [CHOP Class](https://docs.derivative.ca/CHOP_Class) | Channel operators. |
| [SOP Class](https://docs.derivative.ca/SOP_Class) | Surface operators (CPU geometry). |
| [POP Class](https://docs.derivative.ca/POP_Class) | Point operators (GPU geometry). `numPoints()`, `points()`, `pointAttributes()`, `dimension`. |
| [DAT Class](https://docs.derivative.ca/DAT_Class) | Data/table operators (text, tables, scripts). |
| [Par Class](https://docs.derivative.ca/Par_Class) | Parameter object — `.val`, `.expr`, `.mode`, `.enable`. |
| [ParCollection](https://docs.derivative.ca/ParCollection_Class) | `op('x').pars()`. |
| [Cell Class](https://docs.derivative.ca/Cell_Class) | DAT cell (`op('t').cell(row,col)`). |
| [Channel Class](https://docs.derivative.ca/Channel_Class) | CHOP channel object. |
| [Matrix / Vector](https://docs.derivative.ca/Matrix_Class) | Math helper classes. |
| [`ui` Module](https://docs.derivative.ca/UI_Class) | Panels, viewers, windows, `ui.panes`. |
| [`app` Module](https://docs.derivative.ca/App_Class) | Global app state, `app.samples`, `app.config`, `app.opener`. |

### 2c. Scripting Concepts (read these early)
| Link | Description |
|------|-------------|
| [Extensions](https://docs.derivative.ca/Extensions) | Attach a Python class to a COMP (`COMP.extensionX`). The standard way to build reusable modules. |
| [Storage](https://docs.derivative.ca/Storage) | `store()` / `fetch()` — persistent per-node Python data across cooks. |
| [Custom Parameters](https://docs.derivative.ca/Custom_Parameters) | Create your own `.par.MyParam` on any COMP. |
| [Parameter Dependencies](https://docs.derivative.ca/Parameter_Dependencies) | How `me` / `op` / `parent` / `ext` resolve in expressions. |
| [Parameter Mode](https://docs.derivative.ca/Parameter_Mode) | Constant / Expression / Export / Bind modes. |
| [Bindings](https://docs.derivative.ca/Binding) | Two-way parameter binding between components. |
| [Cloning](https://docs.derivative.ca/Cloning) | Clone a master COMP to many instances. |
| [Replicator COMP](https://docs.derivative.ca/Replicator_COMP) | Auto-generate child COMPs from a table/iteration. |
| [Execute DATs](https://docs.derivative.ca/Execute_DAT) | Run Python on events: `onCook`, `onValueChange`, `onPulse`… |
| [Script SOP / CHOP / TOP](https://docs.derivative.ca/Script_SOP) | Generate/modify data procedurally in Python. |
| [Introduction to Python in TD](https://docs.derivative.ca/Introduction_to_Python) | Notes that `numpy` and other libs ship built-in. |

> Tip: Inside TD press `F1` on any node. In the Textport type `help(op('x'))` or `op('x').par` and Tab-complete.

---

## 3. Core Workflow & Debugging Tools (in-app)

| Tool | How to open | Why it matters |
|------|-------------|----------------|
| Middle-click popup | Middle-click any node | Shows cook time, points/CHOP samples, POP attributes, GPU/CPU state. **Read this constantly.** |
| [Textport](https://docs.derivative.ca/Textport) | `Alt+T` / Dialogs | Python REPL + error output. |
| [Performance Monitor](https://docs.derivative.ca/Performance_Monitor) | `Alt+F` / Dialogs | Profile which OPs are slow. |
| [OP Find DAT](https://docs.derivative.ca/OP_Find_DAT) | Search `opfind` | Find OPs by type/name/parameter across the network. |
| [Dialogs → Palette](https://docs.derivative.ca/Palette) | Dialogs | Pre-built widgets, tools, web, mapping helpers. |
| [Dialogs → Component Editor](https://docs.derivative.ca/Component_Editor) | Dialogs | Edit custom parameters / extensions of a COMP. |
| [Error / Warning state](https://docs.derivative.ca/Optimization) | Red/yellow node flag | Hover for the message; broke-cook chain shown in OP. |
| [Probe](https://docs.derivative.ca/Probe) | Drag OP onto Probe | Live-inspect a value without touching the network. |

---

## 4. Key Operators to Know per Family

**TOPs (images/GPU):** [Constant](https://docs.derivative.ca/Constant_TOP), [Movie File In](https://docs.derivative.ca/Movie_File_In_TOP), [Texture 3D](https://docs.derivative.ca/Texture_3D_TOP), [Render](https://docs.derivative.ca/Render_TOP), [Feedback](https://docs.derivative.ca/Feedback_TOP), [Level](https://docs.derivative.ca/Level_TOP), [Composite](https://docs.derivative.ca/Composite_TOP), [GLSL](https://docs.derivative.ca/GLSL_TOP), [Select](https://docs.derivative.ca/Select_TOP), [Null](https://docs.derivative.ca/Null_TOP).

**CHOPs (signals):** [Constant](https://docs.derivative.ca/Constant_CHOP), [LFO](https://docs.derivative.ca/LFO_CHOP), [Noise](https://docs.derivative.ca/Noise_CHOP), [Math](https://docs.derivative.ca/Math_CHOP), [Merge](https://docs.derivative.ca/Merge_CHOP), [OSC In](https://docs.derivative.ca/OSC_In_CHOP), [Timer](https://docs.derivative.ca/Timer_CHOP), [Speed](https://docs.derivative.ca/Speed_CHOP), [Select](https://docs.derivative.ca/Select_CHOP), [Null](https://docs.derivative.ca/Null_CHOP).

**DATs (data/text):** [Table](https://docs.derivative.ca/Table_DAT), [Text](https://docs.derivative.ca/Text_DAT), [Execute](https://docs.derivative.ca/Execute_DAT), [Select](https://docs.derivative.ca/Select_DAT), [OP Find](https://docs.derivative.ca/OP_Find_DAT), [WebSocket](https://docs.derivative.ca/WebSocket_DAT), [JSON](https://docs.derivative.ca/JSON_DAT), [Web](https://docs.derivative.ca/Web_DAT).

**SOPs (CPU geometry):** [Box/Sphere/Grid](https://docs.derivative.ca/Box_SOP), [Noise](https://docs.derivative.ca/Noise_SOP), [Merge](https://docs.derivative.ca/Merge_SOP), [Transform](https://docs.derivative.ca/Transform_SOP), [Script SOP](https://docs.derivative.ca/Script_SOP).

**POPs (GPU geometry):** [Point Generate](https://docs.derivative.ca/Point_Generator_POP), [Math](https://docs.derivative.ca/Math_POP), [Noise](https://docs.derivative.ca/Noise_POP), [Particle](https://docs.derivative.ca/Particle_POP), [Feedback](https://docs.derivative.ca/Feedback_POP), [Copy](https://docs.derivative.ca/Copy_POP), [GLSL](https://docs.derivative.ca/GLSL_POP), [Line](https://docs.derivative.ca/Line_POP), [Attribute](https://docs.derivative.ca/Attribute_POP), [POP to DAT](https://docs.derivative.ca/POP_to_DAT).

**COMPs (networks/UI):** [Container](https://docs.derivative.ca/Container_COMP), [Geometry](https://docs.derivative.ca/Geometry_COMP), [Camera](https://docs.derivative.ca/Camera_COMP), [Light](https://docs.derivative.ca/Light_COMP), [Replicator](https://docs.derivative.ca/Replicator_COMP), [Engine](https://docs.derivative.ca/Engine_COMP), [Web](https://docs.derivative.ca/Web_Comp), [Parameter](https://docs.derivative.ca/Parameter_COMP), [Widget](https://docs.derivative.ca/Widget_COMP).

---

## 5. Official Learning Curriculum (learn.derivative.ca)

The **TouchDesigner Curriculum** — free structured courses by Derivative.

| Link | Description |
|------|-------------|
| [Curriculum Home](https://learn.derivative.ca/) | Landing page for all courses. |
| [100 Series: Fundamentals](https://learn.derivative.ca/courses/100-fundamentals/) | Beginner: UI, all operator families, Python, rendering, COMPs, POPs. |
| [200 Series: Intermediate](https://learn.derivative.ca/courses/200-intermediate/) | Deeper topics after fundamentals. |
| [Learning Tips](https://learn.derivative.ca/learning-resources/learning-tips/) | How to study TD effectively. |
| [Curriculum Navigator](https://learn.derivative.ca/learning-resources/curriculum-navigator/) | Roadmap of what to learn in what order. |
| [Sample Syllabus](https://docs.google.com/document/d/1DrXhmET3MoGWgxPH34VYCUL0uDEwFOLk1zEkEsn5t4w/edit?usp=sharing) | For instructors/classrooms. |
| [Course Examples (.zip)](https://github.com/TouchDesigner/CurriculumExamples/raw/main/toxExamples/_zipped/TouchDesignerFundamentals100Examples.zip) | All example `.toe`/`.tox` files for the 100 series. |

### 100 Series Syllabus
- **101** Navigating the Environment (UI, operator anatomy, references)
- **102** TOPs: Working with Images
- **103** CHOPs: Working With Signals
- **104** Rendering 3D Scenes
- **105** COMPs: Organization & Outputs
- **106** COMPs: Interface Building & Controls
- **107** DATs: Scripting & Python
- **108** Resources, Tips & Tricks (OSC/DMX, OP Snippets, Forum)
- **109** POPs: Working with Points

---

## 6. Official GitHub Repositories

| Link | Description |
|------|-------------|
| [github.com/TouchDesigner](https://github.com/TouchDesigner) | Derivative's official org. |
| [CurriculumExamples](https://github.com/TouchDesigner/CurriculumExamples) | Example networks from the official courses. |
| [All official repos](https://github.com/TouchDesigner?tab=repositories) | Widgets, tools, samples. |

> Derivative also ships sample `.tox` components via the in-app **Palette** (`Dialogs → Palette`).

---

## 7. Community & Support

| Link | Description |
|------|-------------|
| [Derivative Forum](https://forum.derivative.ca/) | Official community forum — Q&A, shared work, bug reports. **Search before posting.** |
| [POPs Intro Video (forum)](https://forum.derivative.ca/t/intro-to-pops-video-from-iihq/519824) | Introductory POPs walkthrough. |
| [r/TouchDesigner](https://www.reddit.com/r/TouchDesigner/) | Community subreddit. |
| [Official YouTube](https://www.youtube.com/TouchDesignerOfficial) | Tutorials and feature videos. |
| [Facebook](https://www.facebook.com/TouchDesigner) / [Instagram](https://www.instagram.com/TouchDesigner/) | Social channels. |
| [Derivative Site](https://derivative.ca/) | Downloads, licenses, commercial info. |
| [Support Service](https://derivative.ca/support-service) | Paid official support. |
| [Community Showcase](https://derivative.ca/community) | Featured user projects. |

---

## 8. Third-Party Tutorials, Creators & Books

| Link | Description |
|------|-------------|
| [Interactive & Immersive HQ (iiHQ)](https://interactiveimmersive.io/) | Elburz Sorkhabi's school — paid courses + high-quality free YouTube. Source of the POPs intro video. |
| [Matthew Ragan](https://matthewragan.com/) | Long-running TD blog, deep technical write-ups and `.tox` sharing. |
| [Bileam Tschepe (breatheheavy)](https://breatheheavy.com/) | Very active YouTube tutorial channel for beginners→intermediate. |
| [Darien Brito](https://www.youtube.com/@DarienBrito) | Recommended POPs / GLSL learning resource. |
| [Elburz on YouTube](https://www.youtube.com/@Elburz) | Veteran TD trainer, many free streams. |
| [Books on Amazon](https://www.amazon.com/s?k=touchdesigner) | Search "TouchDesigner" — several intro/reference books exist (e.g. iiHQ's "Introduction to TouchDesigner"). |
| [Packt](https://www.packtpub.com/) | Occasional TD titles. |

---

## 9. Shared Components & Open-Source Tools

| Link | Description |
|------|-------------|
| [Forum "Operators" sharing](https://forum.derivative.ca/c/operators/8) | Operator-specific `.tox` sharing threads. |
| [Palette → Tools → Widgets](https://docs.derivative.ca/Palette) | Use built-in widgets before building your own UI. |
| [Search ".tox" on GitHub](https://github.com/search?q=touchdesigner+tox) | Community components. |
| [Community Showcase](https://derivative.ca/community) | Curated shared projects. |

---

## 10. Integration & Protocol Interop

| Link | Description |
|------|-------------|
| [TouchEngine](https://docs.derivative.ca/TouchEngine) | Run TD networks inside other apps (Unreal, Notch, etc.). |
| [Unreal Engine](https://docs.derivative.ca/Unreal_Engine) | TouchEngine plugin for UE. |
| [Notch](https://docs.derivative.ca/Notch) | Notch block integration. |
| [OSC CHOP](https://docs.derivative.ca/OSC_In_CHOP) | Open Sound Control. |
| [DMX Out CHOP](https://docs.derivative.ca/DMX_Out_CHOP) | Lighting (Art-Net / sACN). |
| [MIDI CHOP](https://docs.derivative.ca/MIDI_In_CHOP) | MIDI input. |
| [Ableton Link](https://docs.derivative.ca/Ableton_Link_CHOP) | Tempo/sync with Ableton Live. |
| [Spout / Syphon](https://docs.derivative.ca/Spout) | GPU texture sharing between apps. |
| [NDI](https://docs.derivative.ca/NDI) | Network video. |
| [Kinect](https://docs.derivative.ca/Kinect) | Body/depth tracking. |
| [WebSocket DAT](https://docs.derivative.ca/WebSocket_DAT) | Real-time web comms. |
| [Web COMP](https://docs.derivative.ca/Web_Comp) | Embed a browser/render web content. |
| [WebRTC](https://docs.derivative.ca/WebRTC) | Low-latency streaming. |

---

## 11. Events & Conferences

| Link | Description |
|------|-------------|
| [TouchDesigner Summit](https://derivative.ca/community) | Periodic multi-day TD conference (check Derivative community news). |
| [Worldwide Meetups](https://forum.derivative.ca/) | Regional TD user meetups announced on the forum. |
| [Derivative News](https://derivative.ca/) | Build releases, events, showcases. |

---

## 12. Quick-Start Checklist

1. Do the **100 Series** (esp. 101 UI, 107 Python, 109 POPs).
2. Read the **middle-click popup** on every node (cook time + attributes/GPU state).
3. Use **OP Snippets** (`Help → Operator Snippets`) for any unfamiliar node.
4. Keep [OP Class](https://docs.derivative.ca/OP_Class) + [`tdu`](https://docs.derivative.ca/Tdu_Module) open while scripting.
5. Learn **Extensions + Storage** early — they scale your projects.
6. Profile with **Performance Monitor** before optimizing.
7. Search the [forum](https://forum.derivative.ca/) before posting.

---

## 13. Bookmark Summary (raw URLs)

```
# Official docs
https://docs.derivative.ca/
https://docs.derivative.ca/Learning_About_POPs
https://docs.derivative.ca/OP_Snippets
https://docs.derivative.ca/Operator_Family
https://docs.derivative.ca/Category:Operators
https://docs.derivative.ca/Optimization

# Python
https://docs.derivative.ca/Category:Python_Reference
https://docs.derivative.ca/Td_Module
https://docs.derivative.ca/Tdu_Module
https://docs.derivative.ca/TDFunctions
https://docs.derivative.ca/OP_Class
https://docs.derivative.ca/POP_Class
https://docs.derivative.ca/COMP_Class
https://docs.derivative.ca/TOP_Class
https://docs.derivative.ca/CHOP_Class
https://docs.derivative.ca/SOP_Class
https://docs.derivative.ca/DAT_Class
https://docs.derivative.ca/Par_Class
https://docs.derivative.ca/Extensions
https://docs.derivative.ca/Storage
https://docs.derivative.ca/Custom_Parameters

# Curriculum
https://learn.derivative.ca/
https://learn.derivative.ca/courses/100-fundamentals/
https://learn.derivative.ca/courses/200-intermediate/
https://github.com/TouchDesigner/CurriculumExamples

# Community
https://forum.derivative.ca/
https://www.youtube.com/TouchDesignerOfficial
https://www.reddit.com/r/TouchDesigner/
https://interactiveimmersive.io/
https://matthewragan.com/
https://breatheheavy.com/
https://derivative.ca/
```
