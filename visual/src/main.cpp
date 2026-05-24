#include <SDL3/SDL.h>
#include <SDL3/SDL_gpu.h>
#include <SDL3/SDL_main.h>

#include <arpa/inet.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

// ---------------------------------------------------------------------------
// Uniform structs (std140) — must mirror the GLSL declarations exactly.
// ---------------------------------------------------------------------------

// Fragment slot 0: layout(set=3, binding=0)
struct Uniforms {
    float time;
    float aspect;
    float resolution[2];
    float mouse[2];
    float _pad[2];
};
static_assert(sizeof(Uniforms) % 16 == 0);

// Fragment slot 1: layout(set=3, binding=1)
// std140 rules: scalars packed 4-per-16-byte row; float array → each element
// occupies a full vec4 slot, so chroma[12] must be declared vec4 chroma[3].
struct AudioUniforms {
    float volume_db;     // [-80, 0]
    float bass;          // [0, 1]
    float mid;           // [0, 1]
    float treble;        // [0, 1]
    float bpm;           // [-1, 320]; -1 = unknown
    float smoothness;    // [0, 1]
    float centroid_hz;   // [0, 22050]
    float centroid_n;    // [0, 1]
    float note;          // [0, 127] MIDI note number; pitch class = fmod(note, 12.0)
    float note_strength; // chroma energy of dominant note
    float _pad[2];
    float chroma[12];    // pitch-class energies; shader reads as vec4 chroma[3]
};
static_assert(sizeof(AudioUniforms) % 16 == 0);

// ---------------------------------------------------------------------------
// OSC / audio frame
// ---------------------------------------------------------------------------

struct AudioFrame {
    int32_t seq;
    float   t;
    float   total_time;
    float   volume_db;
    float   bass, mid, treble;
    float   bpm;
    float   smoothness;
    float   centroid_hz;
    float   centroid_n;
    int32_t note;
    float   chroma[12];
};

namespace osc {

inline uint32_t be32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16)
         | (uint32_t(p[2]) <<  8) |  uint32_t(p[3]);
}

inline int32_t read_i32(const uint8_t*& p, const uint8_t* end) {
    if (p + 4 > end) throw std::runtime_error("EOF in int");
    int32_t v = static_cast<int32_t>(be32(p)); p += 4;
    return v;
}

inline float read_f32(const uint8_t*& p, const uint8_t* end) {
    if (p + 4 > end) throw std::runtime_error("EOF in float");
    uint32_t raw = be32(p); p += 4;
    float v; std::memcpy(&v, &raw, sizeof v);
    return v;
}

inline std::string_view read_str(const uint8_t*& p, const uint8_t* end) {
    const uint8_t* start = p;
    while (p < end && *p) ++p;
    if (p >= end) throw std::runtime_error("EOF in string");
    std::string_view sv(reinterpret_cast<const char*>(start),
                        static_cast<size_t>(p - start));
    ++p;
    size_t consumed = static_cast<size_t>(p - start);
    p += (4 - (consumed % 4)) % 4;
    return sv;
}

bool parse_audio_frame(const uint8_t* buf, size_t n, AudioFrame& out) {
    const uint8_t* p   = buf;
    const uint8_t* end = buf + n;
    try {
        if (read_str(p, end) != "/audio/frame") return false;
        std::string_view types = read_str(p, end);
        if (types.size() != 25 || types[0] != ',') return false;

        out.seq        = read_i32(p, end);
        out.t          = read_f32(p, end);
        out.total_time = read_f32(p, end);
        out.volume_db  = read_f32(p, end);
        out.bass        = read_f32(p, end);
        out.mid         = read_f32(p, end);
        out.treble      = read_f32(p, end);
        out.bpm         = read_f32(p, end);
        out.smoothness  = read_f32(p, end);
        out.centroid_hz = read_f32(p, end);
        out.centroid_n  = read_f32(p, end);
        out.note        = read_i32(p, end);
        for (int i = 0; i < 12; ++i) out.chroma[i] = read_f32(p, end);
    } catch (...) { return false; }
    return true;
}

int open_udp_listener(int port) {
    int sock = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) return -1;
    ::fcntl(sock, F_SETFL, O_NONBLOCK);
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(static_cast<uint16_t>(port));
    if (::bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof addr) < 0) {
        ::close(sock); return -1;
    }
    return sock;
}

} // namespace osc

// ---------------------------------------------------------------------------
// Shader / GPU helpers
// ---------------------------------------------------------------------------

static std::vector<uint8_t> load_file(const std::string& path) {
    SDL_IOStream* io = SDL_IOFromFile(path.c_str(), "rb");
    if (!io) return {};
    Sint64 size = SDL_GetIOSize(io);
    if (size <= 0) { SDL_CloseIO(io); return {}; }
    std::vector<uint8_t> buf(static_cast<size_t>(size));
    SDL_ReadIO(io, buf.data(), buf.size());
    SDL_CloseIO(io);
    return buf;
}

static SDL_GPUShader* load_shader(SDL_GPUDevice* device,
                                   const std::string& spv_path,
                                   SDL_GPUShaderStage stage,
                                   uint32_t num_uniform_buffers) {
    auto code = load_file(spv_path);
    if (code.empty()) {
        SDL_Log("Failed to load shader: %s", spv_path.c_str());
        return nullptr;
    }
    SDL_GPUShaderCreateInfo info{};
    info.code                = code.data();
    info.code_size           = code.size();
    info.entrypoint          = "main";
    info.format              = SDL_GPU_SHADERFORMAT_SPIRV;
    info.stage               = stage;
    info.num_uniform_buffers = num_uniform_buffers;
    return SDL_CreateGPUShader(device, &info);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int /*argc*/, char* /*argv*/[]) {
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        SDL_Log("SDL_Init: %s", SDL_GetError());
        return EXIT_FAILURE;
    }

    SDL_Window* window = SDL_CreateWindow("visual", 1920, 1200, 0);
    if (!window) { SDL_Log("SDL_CreateWindow: %s", SDL_GetError()); SDL_Quit(); return EXIT_FAILURE; }

    SDL_GPUDevice* device = SDL_CreateGPUDevice(SDL_GPU_SHADERFORMAT_SPIRV, true, nullptr);
    if (!device) { SDL_Log("SDL_CreateGPUDevice: %s", SDL_GetError()); return EXIT_FAILURE; }

    if (!SDL_ClaimWindowForGPUDevice(device, window)) {
        SDL_Log("SDL_ClaimWindowForGPUDevice: %s", SDL_GetError());
        return EXIT_FAILURE;
    }

    const char* base = SDL_GetBasePath();
    std::string shader_dir = base ? std::string(base) + "shaders/" : "shaders/";

    SDL_GPUShader* vert = load_shader(device,
        shader_dir + "fullscreen.vert.spv",
        SDL_GPU_SHADERSTAGE_VERTEX, 0);
    SDL_GPUShader* frag = load_shader(device,
        shader_dir + "default.frag.spv",
        SDL_GPU_SHADERSTAGE_FRAGMENT, 2);  // slot 0: Uniforms, slot 1: AudioUniforms
    if (!vert || !frag) return EXIT_FAILURE;

    SDL_GPUTextureFormat swapchain_fmt =
        SDL_GetGPUSwapchainTextureFormat(device, window);

    SDL_GPUColorTargetDescription color_target{};
    color_target.format = swapchain_fmt;

    SDL_GPUGraphicsPipelineCreateInfo pipeline_info{};
    pipeline_info.vertex_shader   = vert;
    pipeline_info.fragment_shader = frag;
    pipeline_info.primitive_type  = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST;
    pipeline_info.target_info.color_target_descriptions = &color_target;
    pipeline_info.target_info.num_color_targets         = 1;

    SDL_GPUGraphicsPipeline* pipeline =
        SDL_CreateGPUGraphicsPipeline(device, &pipeline_info);
    if (!pipeline) {
        SDL_Log("SDL_CreateGPUGraphicsPipeline: %s", SDL_GetError());
        return EXIT_FAILURE;
    }

    SDL_ReleaseGPUShader(device, vert);
    SDL_ReleaseGPUShader(device, frag);

    // OSC listener (non-blocking; drained each frame).
    int osc_sock = osc::open_udp_listener(9001);
    if (osc_sock < 0) SDL_Log("OSC socket unavailable — audio data will be zeroed");

    int w = 1280, h = 720;
    Uint64 t0 = SDL_GetTicks();
    bool running = true;
    SDL_Event event;
    float mouse_x = 0.f, mouse_y = 0.f;

    Uniforms      u{};
    AudioUniforms audio{};
    audio.bpm = -1.f;  // signal "unknown" until first packet

    while (running) {
        // --- events ---
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_EVENT_QUIT) running = false;
            if (event.type == SDL_EVENT_KEY_DOWN && event.key.key == SDLK_ESCAPE) running = false;
            if (event.type == SDL_EVENT_MOUSE_MOTION) {
                mouse_x = event.motion.x / static_cast<float>(w);
                mouse_y = 1.f - event.motion.y / static_cast<float>(h);
            }
            if (event.type == SDL_EVENT_WINDOW_RESIZED) {
                w = event.window.data1;
                h = event.window.data2;
            }
        }

        // --- drain OSC packets ---
        if (osc_sock >= 0) {
            uint8_t buf[1024];
            ssize_t n;
            while ((n = ::recv(osc_sock, buf, sizeof buf, 0)) > 0) {
                AudioFrame f{};
                if (osc::parse_audio_frame(buf, static_cast<size_t>(n), f)) {
                    audio.volume_db     = f.volume_db;
                    audio.bass          = f.bass;
                    audio.mid           = f.mid;
                    audio.treble        = f.treble;
                    audio.bpm           = f.bpm;
                    audio.smoothness    = f.smoothness;
                    audio.centroid_hz   = f.centroid_hz;
                    audio.centroid_n    = f.centroid_n;
                    audio.note          = static_cast<float>(f.note);
                    audio.note_strength = *std::max_element(f.chroma, f.chroma + 12);
                    std::memcpy(audio.chroma, f.chroma, sizeof f.chroma);
                }
            }
        }

        // --- render ---
        float time_s = static_cast<float>(SDL_GetTicks() - t0) / 1000.f;

        SDL_GPUCommandBuffer* cmdbuf = SDL_AcquireGPUCommandBuffer(device);
        if (!cmdbuf) continue;

        SDL_GPUTexture* swapchain_tex = nullptr;
        Uint32 sw = 0, sh = 0;
        if (!SDL_AcquireGPUSwapchainTexture(cmdbuf, window, &swapchain_tex, &sw, &sh)) {
            SDL_SubmitGPUCommandBuffer(cmdbuf); continue;
        }
        if (!swapchain_tex) { SDL_SubmitGPUCommandBuffer(cmdbuf); continue; }

        SDL_GPUColorTargetInfo ct{};
        ct.texture     = swapchain_tex;
        ct.load_op     = SDL_GPU_LOADOP_CLEAR;
        ct.store_op    = SDL_GPU_STOREOP_STORE;
        ct.clear_color = {0.f, 0.f, 0.f, 1.f};

        SDL_GPURenderPass* pass = SDL_BeginGPURenderPass(cmdbuf, &ct, 1, nullptr);
        SDL_BindGPUGraphicsPipeline(pass, pipeline);

        u.time          = time_s;
        u.aspect        = static_cast<float>(sw) / static_cast<float>(sh);
        u.resolution[0] = static_cast<float>(sw);
        u.resolution[1] = static_cast<float>(sh);
        u.mouse[0]      = mouse_x;
        u.mouse[1]      = mouse_y;
        SDL_PushGPUFragmentUniformData(cmdbuf, 0, &u,     sizeof(u));
        SDL_PushGPUFragmentUniformData(cmdbuf, 1, &audio, sizeof(audio));

        SDL_DrawGPUPrimitives(pass, 3, 1, 0, 0);

        SDL_EndGPURenderPass(pass);
        SDL_SubmitGPUCommandBuffer(cmdbuf);
    }

    if (osc_sock >= 0) ::close(osc_sock);
    SDL_ReleaseGPUGraphicsPipeline(device, pipeline);
    SDL_ReleaseWindowFromGPUDevice(device, window);
    SDL_DestroyGPUDevice(device);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return EXIT_SUCCESS;
}
