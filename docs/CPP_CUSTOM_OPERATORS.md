# Custom C++ operators — the PluginBuilder knowledge base

TouchDesigner can load compiled `.dll` operators (CPlusPlus CHOP / TOP / DAT /
SOP / POP). This page documents the complete pattern — distilled from two
working reference projects in `TD_PROJECTS/PluginBuilder/`:

- **Plugin_FFT** — a production custom CHOP (FFTW3 spectral analysis with a
  hard-real-time cook thread, async worker, perceptual warping).
- **PluginBuilder_V2** — the tool that scaffolds, compiles, hot-reloads and
  deploys such plugins from inside TouchDesigner.

Read this when an agent or user asks about custom operators, C++ plugins, the
CPlusPlus SDK, or when a Python operator is too slow for DSP / pixel work.

---

## 1. What a custom operator IS

A `.dll` exporting three C functions that TouchDesigner discovers on load:

```cpp
extern "C" {
DLLEXPORT void FillCHOPPluginInfo(CHOP_PluginInfo *info)
{
    if (!info->setAPIVersion(CHOPCPlusPlusAPIVersion))   // always the SDK's own version
        return;
    info->customOPInfo.opType->setString("Fftcustom");   // A-Z first char, then a-z/0-9
    info->customOPInfo.opLabel->setString("FFT Custom");
    info->customOPInfo.opIcon->setString("FFT");
    info->customOPInfo.minInputs = 1;  info->customOPInfo.maxInputs = 1;
    info->customOPInfo.majorVersion = 2; info->customOPInfo.minorVersion = 8;
}
DLLEXPORT CHOP_CPlusPlusBase* CreateCHOPInstance(const OP_NodeInfo* info) { return new FFT(info); }
DLLEXPORT void DestroyCHOPInstance(CHOP_CPlusPlusBase* instance) { delete (FFT*)instance; }
}
```

TOPs use the same shape (`FillTOPPluginInfo` / `CreateTOPInstance`), and CUDA
TOPs additionally set `info->executeMode = TOP_ExecuteMode::CUDA;`.

The operator class overrides a virtual base:

| Override | Role |
|---|---|
| `getGeneralInfo(CHOP_GeneralInfo*)` | cooking flags: `cookEveryFrameIfAsked`, `timeslice`, `inputMatchIndex` |
| `getOutputInfo(CHOP_OutputInfo*)` | declare output size (`numChannels`, `numSamples`, `sampleRate`); return `false` to match input instead |
| `getChannelName(int32_t, OP_String*)` | output channel names |
| `execute(CHOP_Output*, const OP_Inputs*)` | **the cook** — read params/inputs, write `output->channels[i][j]` |
| `setupParameters(OP_ParameterManager*)` | register custom parameters |
| `pulsePressed(const char*)` | pulse-button callbacks |
| `getNumInfoCHOPChans` / `getInfoCHOPChan` / `getInfoDATSize` / `getInfoDATEntries` | Info CHOP / Info DAT telemetry |
| `getWarningString` / `getErrorString` | warning / error surfaces (like TD's own) |

## 2. Minimal skeleton (template `BasicCHOP`)

```cpp
void BasicCHOP::getGeneralInfo(CHOP_GeneralInfo* ginfo, const OP_Inputs*, void*)
{
    ginfo->cookEveryFrameIfAsked = true;
    ginfo->timeslice = true;
    ginfo->inputMatchIndex = 0;
}

bool BasicCHOP::getOutputInfo(CHOP_OutputInfo* info, const OP_Inputs* inputs, void*)
{
    if (inputs->getNumInputs() > 0) return false;   // match the input CHOP
    info->numChannels = 1;  info->sampleRate = 120; // generator mode
    return true;
}

void BasicCHOP::execute(CHOP_Output* output, const OP_Inputs* inputs, void*)
{
    double scale = inputs->getParDouble("Scale");
    const OP_CHOPInput *cinput = inputs->getInputCHOP(0);
    for (int i = 0; i < output->numChannels; i++)
        for (int j = 0; j < output->numSamples; j++)
            output->channels[i][j] = float(cinput->getChannelData(i)[j % cinput->numSamples] * scale);
}

void BasicCHOP::setupParameters(OP_ParameterManager* manager, void*)
{
    OP_NumericParameter np;
    np.name = "Speed"; np.label = "Speed";
    np.defaultValues[0] = 1.0; np.minSliders[0] = -10.0; np.maxSliders[0] = 10.0;
    np.help = "Speed of the oscillation.";
    manager->appendFloat(np);
    OP_StringParameter sp; sp.name = "Shape"; sp.defaultValue = "Sine";
    const char* names[] = {"Sine","Square","Ramp"};
    manager->appendMenu(sp, 3, names, labels);
    manager->appendPulse(np);   // Reset button
}
```

**Parameter discipline (the FFT project's idiom).** Every `getPar*` call is a
virtual call (~1–5 µs) — poll parameters **once per cook** into a plain `Values`
struct snapshot and re-use it. Parameter changes land on the next frame. Use
`constexpr` name strings, `enum class` per menu with `static_assert` size
checks, and `inputs->enablePar("Name", 0/1)` for enable/disable.

**Input buffers (CHOP):** `inputs->getInputCHOP(0)` → `.numChannels`,
`.numSamples`, `.sampleRate`, `.getChannelData(i)` (float*), `.getChannelName(i)`.
Time comes from `inputs->getTimeInfo()` (`OP_TimeInfo`, incl. `deltaMS`).

**Output buffers (CHOP):** sizes declared in `getOutputInfo` (TD allocates),
filled in `execute` via `output->channels[ch][j]`.

## 3. TOP buffers — CPU and CUDA

**CPU path** (`CPUMemoryTOP` template):

```cpp
TOP_UploadInfo info; info.textureDesc = {w, h, OP_TexDim::e2D, OP_PixelFormat::RGBA32Float};
OP_SmartRef<TOP_Buffer> buf = myContext->createOutputBuffer(byteSize, TOP_BufferFlags::None, nullptr);
// fill buf->data, then:
output->uploadBuffer(&buf, info, nullptr);
// inputs read back with top->downloadTexture(OP_TOPInputDownloadOptions, nullptr)
// non-stalling: keep the PREVIOUS frame's OP_TOPDownloadResult
```

**CUDA path** (`CudaTOP` template): all `OP_Inputs` calls must happen **before**
`myContext->beginCUDAOperations(nullptr)`; wrap `createCUDAArray` results in
`cudaSurfaceObject_t`, run the kernel on your own `cudaStream_t`, then
`endCUDAOperations`. GL work is prohibited in constructors ("no OpenGL during
instantiation").

## 4. Threading — the hard real-time cook pattern

The Plugin_FFT architecture is the reference for any heavy per-frame work:

- **The cook thread (`execute`) is hard real-time**: no mutex, no condvar, no
  kernel call, no allocation, no Python after warm-up.
- **One background worker**, handoff is wait-free via `TripleBuffer<T>`: three
  rotating slots; one atomic word packs slot-index (bits 0-1) + dirty (bit 2),
  so `publish()` and `acquire()` are each a **single `exchange()`** —
  latest-wins, torn-free. No new result ⇒ re-output the previous one (hold).
- **Worker wake-up**: auto-reset Win32 Event + high-resolution waitable timer
  (`CREATE_WAITABLE_TIMER_HIGH_RESOLUTION`). Hot worker polls every 2 ms; only
  after 500 ms dormant does the cook pay one `signal()` (Dekker handshake for
  the dormancy flip).
- Worker thread names itself and boosts to `THREAD_PRIORITY_HIGHEST`.
- Per-channel fan-out with `std::execution::par` **only when
  `numChannels > 1`** — measured 13–51% *slower* for a single 1-D FFT.
- Telemetry crosses threads via `std::atomic` (relaxed); a status mutex is
  never taken on the cook path. `DenormalGuard` (FTZ/DAZ) on both threads.
- `execute()` wraps everything in try/catch → `myErrorText` → `getErrorString`,
  and zeroes output on failure. Check AVX2 support before using it (an illegal
  instruction crashes TD, not just the op). `getWarningString` warns when the
  async worker falls behind.

## 5. FFTW specifics (Plugin_FFT)

- FFTW3 float R2C, plan policies Auto/Fast/Measured/Patient; *Auto* starts with
  an `FFTW_ESTIMATE` plan and upgrades to a measured plan **on a background
  thread**, wisdom cached to `%LOCALAPPDATA%\TD_Custom_FFT\fftwf_wisdom.txt`.
- The FFTW planner is **process-global and mutex-protected**; FFTW threads
  (`fftwf_plan_with_nthreads`) once crashed TD — avoid mixing FFTW threading
  with TD's own threading.
- Pipeline: window (Kaiser/Hann, coherent-gain normalization) → zero-padded
  frame → RFFT → magnitude (AVX2 rsqrt) → perceptual warp (Log/Mel/ERB/Bark/
  Chroma, gather tables, identity ⇒ memcpy) → A/C/ITU-468 weighting → dB via a
  2048-entry LUT → attack/release ballistics using `OP_TimeInfo::deltaMS`.

## 6. Build & hot-reload workflow (PluginBuilder)

Each plugin project is deliberately tiny — CMake + Ninja + MSVC:

```cmake
cmake_minimum_required(VERSION 3.21)
project(FFT LANGUAGES CXX)
include(${PLUGIN_BUILDER_DIR}/cmake/TDPlugin.cmake)
td_add_plugin(FFT FAMILY CHOP)
td_plugin_use_fftw3(FFT)                    # vendored 3rdParty/fftw3, DLL auto-deployed
td_plugin_optimize(FFT AVX2 FAST_MATH)      # /O2 /fp:fast /arch:AVX2
td_plugin_add_test(FFT fft_tests SOURCES tests/dsp_tests.cpp)  # headless ctest, no TD needed
```

The PluginBuilder.tox workflow inside TouchDesigner:

1. **Setup** once (`SetSettings.toe` → `%APPDATA%/IntentDev/PluginBuilder/settings.ini`; toolchain auto-discovered via `vswhere` + `vcvarsall.bat x64`).
2. **Create** — scaffolds `PluginProjects/<Name>/` from a template (BasicCHOP, BasicDAT, CPUMemoryTOP, CudaTOP, SimpleShapesSOP/POP, CudaPOP, …), builds the loader chain `in1 → plugin_loader (CPlusPlus OP) → out1`.
3. **Build** — persistent background `BuildRunner` (thread + Windows Job Object kill-on-close) submits CMake configure (hash-gated) then Ninja; output regex-parsed into diagnostics.
4. **Hot reload** — the DLL is swapped into `__Plugins__/<Name>/` **only if SHA-256 changed**, using rename-in-place (a DLL mapped by TD cannot be overwritten but *can* be renamed → old becomes `.old`); then `unloadplugin=1` → repoint `par.plugin` → `unloadplugin=0` → pulse `reinitpulse`, preserving parameter values/expressions/bindings.
5. **Iterate** — Compile-On-Update watches `source/` via folder DATs: every save triggers ninja → deploy → reload in about a second.
6. **Install** — copies to `~/Documents/Derivative/Plugins/<Name>/` for global availability.

Standalone, no TouchDesigner: `vcvarsall.bat x64 && cmake -B build -G Ninja && ninja -C build && ctest --test-dir build`.

## 7. When to choose C++ over a Python operator

- Per-sample DSP, per-pixel CPU image work, or anything allocating
  megabytes per cook — a Python Script CHOP/TOP cannot hold real-time rates.
- Need FFTW / other native libraries, SIMD, or precise worker-thread control.
- Everything else stays Python (and TD_MCP's live tools): the C++ route costs
  a compile toolchain and a versioned SDK header set.

## 8. Source index

| Topic | File |
|---|---|
| DLL exports / plugin info | `Plugin_FFT\PluginProjects\FFT\source\FFT.cpp`, `PluginBuilder_V2\templates\BasicCHOP\source\BasicCHOP.cpp` |
| CHOP virtual interface | `PluginBuilder_V2\include\CHOP_CPlusPlusBase.h`, `FFT.h` |
| Parameters (template / production) | `BasicCHOP.cpp`, `FFT\source\Parameters.cpp` (`setup()` / `eval()`) |
| Output channel writing | `FFT.cpp` `copyResultsToOutput` |
| Info CHOP/DAT telemetry | `FFT.cpp` (info chan/entries methods) |
| CPU texture upload / producer thread | `PluginBuilder_V2\templates\CPUMemoryTOP\source\CPUMemoryTOP.cpp` |
| CUDA interop | `PluginBuilder_V2\templates\CudaTOP\source\CudaTOP.cpp` + `kernel.cu` |
| CMake module | `PluginBuilder_V2\cmake\TDPlugin.cmake` |
| Builder workflow | `PluginBuilder_V2\source\PluginBuilderExt.py`, `PluginBuilderCore.py` (`BuildRunner`, `safe_replace_file`) |
| Lock-free handoff / worker wake / FFTW engine | `Plugin_FFT\...\source\DSPModules.h` (`TripleBuffer`, `WorkerSignal`, `FFTWEngine`) |
