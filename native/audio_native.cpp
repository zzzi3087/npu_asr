// Bounded audio gain kernels. Distributed under the project's Apache-2.0 license.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>

struct GainState {
    double gain, input_db, output_db, manual, maximum, target, ceiling, attack, release;
    std::int32_t enabled, limited, block_size;
};
#define API extern "C" __declspec(dllexport)
API std::uint32_t lc_audio_abi() { return 1; }
API std::size_t lc_gain_state_size() { return sizeof(GainState); }

static void measure(const float* audio, std::size_t count, double& rms, double& peak) {
    double sum=0.; peak=0.;
    for (std::size_t i=0;i<count;++i) {
        const double value=audio[i]; sum+=value*value; peak=std::max(peak,std::abs(value));
    }
    rms=std::sqrt(sum/count);
}

API double lc_quiet_gain(const float* audio, std::size_t count, double maximum, double target) {
    if (!audio || !count) return 1.;
    double rms,peak;measure(audio,count,rms,peak);
    if (rms<1e-9) return 1.;
    return std::max(1.,std::min({maximum,target/rms,.9/std::max(peak,1e-9)}));
}

API int lc_gain_process(GainState* state,const float* input,float* output,std::size_t count,int speech) {
    if (!state || !input || !output || !count || count>16384) return 0;
    auto& s=*state;
    if (s.block_size!=static_cast<std::int32_t>(count)) {
        s.block_size=static_cast<std::int32_t>(count);
        s.attack=std::exp(-double(count)/(16000*.025));
        s.release=std::exp(-double(count)/(16000*.35));
    }
    double rms,peak;measure(input,count,rms,peak);
    s.input_db=20*std::log10(std::max(rms,1e-6));s.limited=0;
    double desired=s.manual;
    if (s.enabled) {
        desired=s.gain;
        if (speech && rms>1e-8) desired=std::min(s.maximum,s.target/rms)*s.manual;
        constexpr double minimum_gain=0.06309573444801933; // -24 dB
        desired=std::max(minimum_gain,std::min(desired,s.maximum));
    }
    if (peak>1e-9) desired=std::min(desired,s.ceiling/peak);
    double previous=s.gain;
    const double coefficient=desired<previous?s.attack:s.release;
    s.gain=desired+(previous-desired)*coefficient;
    if (peak*s.gain>s.ceiling) {
        s.gain=s.ceiling/peak;previous=std::min(previous,s.gain);s.limited=1;
    }
    const float delta=static_cast<float>(s.gain-previous),base=static_cast<float>(previous);
    double output_peak=0.,sum=0.;
    for (std::size_t i=0;i<count;++i) {
        const float ramp=count>1?static_cast<float>(double(i)/(count-1)):0.f;
        const float value=input[i]*(base+ramp*delta);
        output[i]=value;output_peak=std::max(output_peak,std::abs(double(value)));sum+=double(value)*value;
    }
    if (output_peak>s.ceiling) {
        const float scale=static_cast<float>(s.ceiling/output_peak);
        for (std::size_t i=0;i<count;++i) output[i]*=scale;
        sum*=double(scale)*scale;s.limited=1;
    }
    s.output_db=20*std::log10(std::max(std::sqrt(sum/count),1e-6));
    return 1;
}
