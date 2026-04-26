package com.xiaozhi.ai.llm.factory.providers;

import com.xiaozhi.ai.llm.factory.ChatModelProvider;
import com.xiaozhi.common.model.bo.ConfigBO;
import com.xiaozhi.common.model.bo.RoleBO;
import io.micrometer.observation.ObservationRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.document.MetadataMode;
import org.springframework.ai.embedding.EmbeddingModel;
import org.springframework.ai.model.NoopApiKey;
import org.springframework.ai.model.SimpleApiKey;
import org.springframework.ai.model.tool.ToolCallingManager;
import org.springframework.ai.openai.OpenAiChatModel;
import org.springframework.ai.openai.OpenAiChatOptions;
import org.springframework.ai.openai.OpenAiEmbeddingModel;
import org.springframework.ai.openai.OpenAiEmbeddingOptions;
import org.springframework.ai.openai.api.OpenAiApi;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.http.client.reactive.JdkClientHttpConnector;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.util.StringUtils;
import org.springframework.web.client.RestClient;
import org.springframework.web.reactive.function.client.WebClient;

import java.net.http.HttpClient;
import java.time.Duration;

/**
 * OpenAI及兼容OpenAI协议的模型提供者
 * 支持: OpenAI, Azure OpenAI, 各种兼容OpenAI的本地模型等
 */
@Component
public class OpenAiModelProvider implements ChatModelProvider {
    
    private static final Logger logger = LoggerFactory.getLogger(OpenAiModelProvider.class);

    @Lazy
    @Autowired
    private ToolCallingManager toolCallingManager;

    @Autowired
    private ObservationRegistry observationRegistry;
    
    @Override
    public String getProviderName() {
        return "openai";
    }
    
    @Override
    public ChatModel createChatModel(ConfigBO config, RoleBO role) {
        String endpoint = config.getApiUrl();
        String apiKey = config.getApiKey();
        String model = config.getConfigName();
        Double temperature = role.getTemperature();
        Double topP = role.getTopP();

        // 注意：不要手动添加 Content-Type 头！
        // Spring AI 的 OpenAiApi 内部会通过 setContentType(MediaType.APPLICATION_JSON) 自动设置（OpenAiApi.java:148），
        // 然后用 h.addAll(headers) 把外部传入的 headers 也加上去（OpenAiApi.java:150），
        // 如果外部再传 Content-Type，会导致请求出现两个 Content-Type，
        // aiohttp 等严格的网关会返回 400 "Duplicate 'Content-Type' header found."

        // LM Studio不支持Http/2，所以需要强制使用HTTP/1.1
        var openAiApi = OpenAiApi.builder()
                .apiKey(StringUtils.hasText(apiKey) ? new SimpleApiKey(apiKey) : new NoopApiKey())
                .baseUrl(endpoint)
                .completionsPath("/chat/completions")
                .webClientBuilder(WebClient.builder()
                        // Force HTTP/1.1 for streaming
                        .clientConnector(new JdkClientHttpConnector(HttpClient.newBuilder()
                                .version(HttpClient.Version.HTTP_1_1)
                                .connectTimeout(Duration.ofSeconds(30))
                                .build())))
                .restClientBuilder(RestClient.builder()
                        .requestFactory(createRequestFactory()))
                .build();
        
        var openAiChatOptions = OpenAiChatOptions.builder()
                .model(model)
                .temperature(temperature)
                .topP(topP)
                .maxCompletionTokens(2000)
                .streamUsage(true)
                .build();
        
        var chatModel = OpenAiChatModel.builder()
                .openAiApi(openAiApi)
                .defaultOptions(openAiChatOptions)
                .toolCallingManager(toolCallingManager)
                .observationRegistry(observationRegistry)
                .build();
        
        logger.info("Created OpenAI ChatModel: model={}, endpoint={}", model, endpoint);
        return chatModel;
    }

    @Override
    public EmbeddingModel createEmbeddingModel(ConfigBO config) {
        // 同 createChatModel：不要手动添加 Content-Type 头，Spring AI 内部会自动设置
        var openAiApi = OpenAiApi.builder()
                .apiKey(StringUtils.hasText(config.getApiKey()) ? new SimpleApiKey(config.getApiKey()) : new NoopApiKey())
                .baseUrl(config.getApiUrl())
                .embeddingsPath("/embeddings")
                .webClientBuilder(WebClient.builder()
                        .clientConnector(new JdkClientHttpConnector(HttpClient.newBuilder()
                                .version(HttpClient.Version.HTTP_1_1)
                                .connectTimeout(Duration.ofSeconds(30))
                                .build())))
                .restClientBuilder(RestClient.builder()
                        .requestFactory(createRequestFactory()))
                .build();
        var options = OpenAiEmbeddingOptions.builder().model(config.getConfigName()).build();
        logger.debug("创建 OpenAI EmbeddingModel: model={}, endpoint={}", config.getConfigName(), config.getApiUrl());
        return new OpenAiEmbeddingModel(openAiApi, MetadataMode.EMBED, options);
    }

    private JdkClientHttpRequestFactory createRequestFactory() {
        var factory = new JdkClientHttpRequestFactory(HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(30))
                .build());
        factory.setReadTimeout(Duration.ofSeconds(30));
        return factory;
    }
}

