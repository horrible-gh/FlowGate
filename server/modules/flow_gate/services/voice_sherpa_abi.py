# Generated ctypes layout from sherpa-onnx v1.12.20 c-api.h.
# Copyright (c) 2022-2025 Xiaomi Corporation; Apache-2.0.
# https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.20/sherpa-onnx/c-api/c-api.h
# Keep the ABI and the pinned runtime release in voice_catalog.json in sync.
from ctypes import Structure, c_int32, c_float, c_char_p

class SherpaOnnxFeatureConfig(Structure):
    _fields_ = [
        ('sample_rate', c_int32),
        ('feature_dim', c_int32),
    ]

class SherpaOnnxOnlineTransducerModelConfig(Structure):
    _fields_ = [
        ('encoder', c_char_p),
        ('decoder', c_char_p),
        ('joiner', c_char_p),
    ]

class SherpaOnnxOnlineParaformerModelConfig(Structure):
    _fields_ = [
        ('encoder', c_char_p),
        ('decoder', c_char_p),
    ]

class SherpaOnnxOnlineZipformer2CtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOnlineNemoCtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOnlineToneCtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOnlineModelConfig(Structure):
    _fields_ = [
        ('transducer', SherpaOnnxOnlineTransducerModelConfig),
        ('paraformer', SherpaOnnxOnlineParaformerModelConfig),
        ('zipformer2_ctc', SherpaOnnxOnlineZipformer2CtcModelConfig),
        ('tokens', c_char_p),
        ('num_threads', c_int32),
        ('provider', c_char_p),
        ('debug', c_int32),
        ('model_type', c_char_p),
        ('modeling_unit', c_char_p),
        ('bpe_vocab', c_char_p),
        ('tokens_buf', c_char_p),
        ('tokens_buf_size', c_int32),
        ('nemo_ctc', SherpaOnnxOnlineNemoCtcModelConfig),
        ('t_one_ctc', SherpaOnnxOnlineToneCtcModelConfig),
    ]

class SherpaOnnxOnlineCtcFstDecoderConfig(Structure):
    _fields_ = [
        ('graph', c_char_p),
        ('max_active', c_int32),
    ]

class SherpaOnnxHomophoneReplacerConfig(Structure):
    _fields_ = [
        ('dict_dir', c_char_p),
        ('lexicon', c_char_p),
        ('rule_fsts', c_char_p),
    ]

class SherpaOnnxOnlineRecognizerConfig(Structure):
    _fields_ = [
        ('feat_config', SherpaOnnxFeatureConfig),
        ('model_config', SherpaOnnxOnlineModelConfig),
        ('decoding_method', c_char_p),
        ('max_active_paths', c_int32),
        ('enable_endpoint', c_int32),
        ('rule1_min_trailing_silence', c_float),
        ('rule2_min_trailing_silence', c_float),
        ('rule3_min_utterance_length', c_float),
        ('hotwords_file', c_char_p),
        ('hotwords_score', c_float),
        ('ctc_fst_decoder_config', SherpaOnnxOnlineCtcFstDecoderConfig),
        ('rule_fsts', c_char_p),
        ('rule_fars', c_char_p),
        ('blank_penalty', c_float),
        ('hotwords_buf', c_char_p),
        ('hotwords_buf_size', c_int32),
        ('hr', SherpaOnnxHomophoneReplacerConfig),
    ]

class SherpaOnnxOfflineTransducerModelConfig(Structure):
    _fields_ = [
        ('encoder', c_char_p),
        ('decoder', c_char_p),
        ('joiner', c_char_p),
    ]

class SherpaOnnxOfflineParaformerModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineNemoEncDecCtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineWhisperModelConfig(Structure):
    _fields_ = [
        ('encoder', c_char_p),
        ('decoder', c_char_p),
        ('language', c_char_p),
        ('task', c_char_p),
        ('tail_paddings', c_int32),
    ]

class SherpaOnnxOfflineTdnnModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineSenseVoiceModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
        ('language', c_char_p),
        ('use_itn', c_int32),
    ]

class SherpaOnnxOfflineMoonshineModelConfig(Structure):
    _fields_ = [
        ('preprocessor', c_char_p),
        ('encoder', c_char_p),
        ('uncached_decoder', c_char_p),
        ('cached_decoder', c_char_p),
    ]

class SherpaOnnxOfflineFireRedAsrModelConfig(Structure):
    _fields_ = [
        ('encoder', c_char_p),
        ('decoder', c_char_p),
    ]

class SherpaOnnxOfflineDolphinModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineZipformerCtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineCanaryModelConfig(Structure):
    _fields_ = [
        ('encoder', c_char_p),
        ('decoder', c_char_p),
        ('src_lang', c_char_p),
        ('tgt_lang', c_char_p),
        ('use_pnc', c_int32),
    ]

class SherpaOnnxOfflineWenetCtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineOmnilingualAsrCtcModelConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
    ]

class SherpaOnnxOfflineModelConfig(Structure):
    _fields_ = [
        ('transducer', SherpaOnnxOfflineTransducerModelConfig),
        ('paraformer', SherpaOnnxOfflineParaformerModelConfig),
        ('nemo_ctc', SherpaOnnxOfflineNemoEncDecCtcModelConfig),
        ('whisper', SherpaOnnxOfflineWhisperModelConfig),
        ('tdnn', SherpaOnnxOfflineTdnnModelConfig),
        ('tokens', c_char_p),
        ('num_threads', c_int32),
        ('debug', c_int32),
        ('provider', c_char_p),
        ('model_type', c_char_p),
        ('modeling_unit', c_char_p),
        ('bpe_vocab', c_char_p),
        ('telespeech_ctc', c_char_p),
        ('sense_voice', SherpaOnnxOfflineSenseVoiceModelConfig),
        ('moonshine', SherpaOnnxOfflineMoonshineModelConfig),
        ('fire_red_asr', SherpaOnnxOfflineFireRedAsrModelConfig),
        ('dolphin', SherpaOnnxOfflineDolphinModelConfig),
        ('zipformer_ctc', SherpaOnnxOfflineZipformerCtcModelConfig),
        ('canary', SherpaOnnxOfflineCanaryModelConfig),
        ('wenet_ctc', SherpaOnnxOfflineWenetCtcModelConfig),
        ('omnilingual', SherpaOnnxOfflineOmnilingualAsrCtcModelConfig),
    ]

class SherpaOnnxOfflineLMConfig(Structure):
    _fields_ = [
        ('model', c_char_p),
        ('scale', c_float),
    ]

class SherpaOnnxOfflineRecognizerConfig(Structure):
    _fields_ = [
        ('feat_config', SherpaOnnxFeatureConfig),
        ('model_config', SherpaOnnxOfflineModelConfig),
        ('lm_config', SherpaOnnxOfflineLMConfig),
        ('decoding_method', c_char_p),
        ('max_active_paths', c_int32),
        ('hotwords_file', c_char_p),
        ('hotwords_score', c_float),
        ('rule_fsts', c_char_p),
        ('rule_fars', c_char_p),
        ('blank_penalty', c_float),
        ('hr', SherpaOnnxHomophoneReplacerConfig),
    ]
