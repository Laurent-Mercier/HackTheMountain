#include <SDL3/SDL.h>
#include <SDL3/SDL_gpu.h>
#include <SDL3/SDL_main.h>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

// std140-compatible uniform block — mirrors default.frag layout(set=3,binding=0)
struct Uniforms {
    float time;
    float aspect;
    float resolution[2];
    float mouse[2];
    float _pad[2];
};

static_assert(sizeof(Uniforms) % 16 == 0);

static std::vector<Uint8> load_file(const std::string& path) {
    SDL_IOStream* io = SDL_IOFromFile(path.c_str(), "rb");
    if (!io) return {};
    Sint64 size = SDL_GetIOSize(io);
    if (size <= 0) { SDL_CloseIO(io); return {}; }
    std::vector<Uint8> buf(static_cast<size_t>(size));
    SDL_ReadIO(io, buf.data(), buf.size());
    SDL_CloseIO(io);
    return buf;
}

static SDL_GPUShader* load_shader(SDL_GPUDevice* device,
                                   const std::string& spv_path,
                                   SDL_GPUShaderStage stage,
                                   Uint32 num_uniform_buffers) {
    auto code = load_file(spv_path);
    if (code.empty()) {
        SDL_Log("Failed to load shader: %s", spv_path.c_str());
        return nullptr;
    }
    SDL_GPUShaderCreateInfo info{};
    info.code              = code.data();
    info.code_size         = code.size();
    info.entrypoint        = "main";
    info.format            = SDL_GPU_SHADERFORMAT_SPIRV;
    info.stage             = stage;
    info.num_uniform_buffers = num_uniform_buffers;
    return SDL_CreateGPUShader(device, &info);
}

int main(int /*argc*/, char* /*argv*/[]) {
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        SDL_Log("SDL_Init: %s", SDL_GetError());
        return EXIT_FAILURE;
    }

    SDL_Window* window = SDL_CreateWindow("visual", 1280, 720, 0);
    if (!window) { SDL_Log("SDL_CreateWindow: %s", SDL_GetError()); SDL_Quit(); return EXIT_FAILURE; }

    SDL_GPUDevice* device = SDL_CreateGPUDevice(SDL_GPU_SHADERFORMAT_SPIRV, true, nullptr);
    if (!device) { SDL_Log("SDL_CreateGPUDevice: %s", SDL_GetError()); return EXIT_FAILURE; }

    if (!SDL_ClaimWindowForGPUDevice(device, window)) {
        SDL_Log("SDL_ClaimWindowForGPUDevice: %s", SDL_GetError());
        return EXIT_FAILURE;
    }

    // Locate shader directory relative to the binary.
    const char* base = SDL_GetBasePath();
    std::string shader_dir = base ? std::string(base) + "shaders/" : "shaders/";

    SDL_GPUShader* vert = load_shader(device,
        shader_dir + "fullscreen.vert.spv",
        SDL_GPU_SHADERSTAGE_VERTEX, 0);
    SDL_GPUShader* frag = load_shader(device,
        shader_dir + "default.frag.spv",
        SDL_GPU_SHADERSTAGE_FRAGMENT, 1);
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

    int w = 1280, h = 720;
    Uint64 t0 = SDL_GetTicks();
    bool running = true;
    SDL_Event event;
    float mouse_x = 0.f, mouse_y = 0.f;

    while (running) {
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

        float time_s = static_cast<float>(SDL_GetTicks() - t0) / 1000.f;

        SDL_GPUCommandBuffer* cmdbuf = SDL_AcquireGPUCommandBuffer(device);
        if (!cmdbuf) continue;

        SDL_GPUTexture* swapchain_tex = nullptr;
        Uint32 sw = 0, sh = 0;
        if (!SDL_AcquireGPUSwapchainTexture(cmdbuf, window, &swapchain_tex, &sw, &sh)) {
            SDL_SubmitGPUCommandBuffer(cmdbuf);
            continue;
        }
        if (!swapchain_tex) { SDL_SubmitGPUCommandBuffer(cmdbuf); continue; }

        SDL_GPUColorTargetInfo ct{};
        ct.texture     = swapchain_tex;
        ct.load_op     = SDL_GPU_LOADOP_CLEAR;
        ct.store_op    = SDL_GPU_STOREOP_STORE;
        ct.clear_color = {0.f, 0.f, 0.f, 1.f};

        SDL_GPURenderPass* pass = SDL_BeginGPURenderPass(cmdbuf, &ct, 1, nullptr);

        SDL_BindGPUGraphicsPipeline(pass, pipeline);

        Uniforms u{};
        u.time          = time_s;
        u.aspect        = static_cast<float>(sw) / static_cast<float>(sh);
        u.resolution[0] = static_cast<float>(sw);
        u.resolution[1] = static_cast<float>(sh);
        u.mouse[0]      = mouse_x;
        u.mouse[1]      = mouse_y;
        SDL_PushGPUFragmentUniformData(cmdbuf, 0, &u, sizeof(u));

        SDL_DrawGPUPrimitives(pass, 3, 1, 0, 0);

        SDL_EndGPURenderPass(pass);
        SDL_SubmitGPUCommandBuffer(cmdbuf);
    }

    SDL_ReleaseGPUGraphicsPipeline(device, pipeline);
    SDL_ReleaseWindowFromGPUDevice(device, window);
    SDL_DestroyGPUDevice(device);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return EXIT_SUCCESS;
}
