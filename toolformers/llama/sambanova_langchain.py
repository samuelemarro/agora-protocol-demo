"""Langchain Wrapper around Sambanova LLM APIs."""

import json
from typing import Any, Dict, Generator, Iterator, List, Optional, Union

import requests
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.llms import LLM
from langchain_core.outputs import GenerationChunk
from langchain_core.pydantic_v1 import Extra
from langchain_core.utils import get_from_dict_or_env, pre_init
from langchain_core.runnables import RunnableConfig, ensure_config
from langchain_core.language_models.base import (
    LanguageModelInput,
)
from toolformers.llama.utils import append_to_usage_tracker


class SSEndpointHandler:
    """
    SambaNova Systems Interface for SambaStudio model endpoints.

    :param str host_url: Base URL of the DaaS API service
    """

    def __init__(self, host_url: str, api_base_uri: str):
        """
        Initialize the SSEndpointHandler.

        :param str host_url: Base URL of the DaaS API service
        :param str api_base_uri: Base URI of the DaaS API service
        """
        self.host_url = host_url
        self.api_base_uri = api_base_uri
        self.http_session = requests.Session()

    def _process_response(self, response: requests.Response) -> Dict:
        """
        Processes the API response and returns the resulting dict.

        All resulting dicts, regardless of success or failure, will contain the
        `status_code` key with the API response status code.

        If the API returned an error, the resulting dict will contain the key
        `detail` with the error message.

        If the API call was successful, the resulting dict will contain the key
        `data` with the response data.

        :param requests.Response response: the response object to process
        :return: the response dict
        :type: dict
        """
        result: Dict[str, Any] = {}
        try:
            result = response.json()
        except Exception as e:
            result['detail'] = str(e)
        if 'status_code' not in result:
            result['status_code'] = response.status_code
        return result

    def _process_streaming_response(
        self,
        response: requests.Response,
    ) -> Generator[Dict, None, None]:
        """Process the streaming response"""
        if 'api/predict/nlp' in self.api_base_uri:
            try:
                import sseclient
            except ImportError:
                raise ImportError(
                    'could not import sseclient library' 'Please install it with `pip install sseclient-py`.'
                )
            client = sseclient.SSEClient(response)
            close_conn = False
            for event in client.events():
                if event.event == 'error_event':
                    close_conn = True
                chunk = {
                    'event': event.event,
                    'data': event.data,
                    'status_code': response.status_code,
                }
                yield chunk
            if close_conn:
                client.close()
        elif 'api/v2/predict/generic' in self.api_base_uri or 'api/predict/generic' in self.api_base_uri:
            try:
                for line in response.iter_lines():
                    chunk = json.loads(line)
                    if 'status_code' not in chunk:
                        chunk['status_code'] = response.status_code
                    yield chunk
            except Exception as e:
                raise RuntimeError(f'Error processing streaming response: {e}')
        else:
            raise ValueError(f'handling of endpoint uri: {self.api_base_uri} not implemented')

    def _get_full_url(self, path: str) -> str:
        """
        Return the full API URL for a given path.

        :param str path: the sub-path
        :returns: the full API URL for the sub-path
        :type: str
        """
        return f'{self.host_url}/{self.api_base_uri}/{path}'

    def nlp_predict(
        self,
        project: str,
        endpoint: str,
        key: str,
        input: Union[List[str], str],
        params: Optional[str] = '',
        stream: bool = False,
    ) -> Dict:
        """
        NLP predict using inline input string.

        :param str project: Project ID in which the endpoint exists
        :param str endpoint: Endpoint ID
        :param str key: API Key
        :param str input_str: Input string
        :param str params: Input params string
        :returns: Prediction results
        :type: dict
        """
        if isinstance(input, str):
            input = [input]
        if 'api/predict/nlp' in self.api_base_uri:
            if params:
                data = {'inputs': input, 'params': json.loads(params)}
            else:
                data = {'inputs': input}
        elif 'api/v2/predict/generic' in self.api_base_uri:
            items = [{'id': f'item{i}', 'value': item} for i, item in enumerate(input)]
            if params:
                data = {'items': items, 'params': json.loads(params)}
            else:
                data = {'items': items}
        elif 'api/predict/generic' in self.api_base_uri:
            if params:
                data = {'instances': input, 'params': json.loads(params)}
            else:
                data = {'instances': input}
        else:
            raise ValueError(f'handling of endpoint uri: {self.api_base_uri} not implemented')
        response = self.http_session.post(
            self._get_full_url(f'{project}/{endpoint}'),
            headers={'key': key},
            json=data,
        )
        return self._process_response(response)

    def nlp_predict_stream(
        self,
        project: str,
        endpoint: str,
        key: str,
        input: Union[List[str], str],
        params: Optional[str] = '',
    ) -> Iterator[Dict]:
        """
        NLP predict using inline input string.

        :param str project: Project ID in which the endpoint exists
        :param str endpoint: Endpoint ID
        :param str key: API Key
        :param str input_str: Input string
        :param str params: Input params string
        :returns: Prediction results
        :type: dict
        """
        if 'api/predict/nlp' in self.api_base_uri:
            if isinstance(input, str):
                input = [input]
            if params:
                data = {'inputs': input, 'params': json.loads(params)}
            else:
                data = {'inputs': input}
        elif 'api/v2/predict/generic' in self.api_base_uri:
            if isinstance(input, str):
                input = [input]
            items = [{'id': f'item{i}', 'value': item} for i, item in enumerate(input)]
            if params:
                data = {'items': items, 'params': json.loads(params)}
            else:
                data = {'items': items}
        elif 'api/predict/generic' in self.api_base_uri:
            if isinstance(input, list):
                input = input[0]
            if params:
                data = {'instance': input, 'params': json.loads(params)}
            else:
                data = {'instance': input}
        else:
            raise ValueError(f'handling of endpoint uri: {self.api_base_uri} not implemented')
        # Streaming output
        response = self.http_session.post(
            self._get_full_url(f'stream/{project}/{endpoint}'),
            headers={'key': key},
            json=data,
            stream=True,
        )
        for chunk in self._process_streaming_response(response):
            yield chunk


class SambaStudio(LLM):
    """
    SambaStudio large language models.

    To use, you should have the environment variables
    ``SAMBASTUDIO_BASE_URL`` set with your SambaStudio environment URL.
    ``SAMBASTUDIO_BASE_URI`` set with your SambaStudio api base URI.
    ``SAMBASTUDIO_PROJECT_ID`` set with your SambaStudio project ID.
    ``SAMBASTUDIO_ENDPOINT_ID`` set with your SambaStudio endpoint ID.
    ``SAMBASTUDIO_API_KEY``  set with your SambaStudio endpoint API key.

    https://sambanova.ai/products/enterprise-ai-platform-sambanova-suite

    read extra documentation in https://docs.sambanova.ai/sambastudio/latest/index.html

    Example:
    .. code-block:: python

        from langchain_community.llms.sambanova  import SambaStudio
        SambaStudio(
            sambastudio_base_url="your-SambaStudio-environment-URL",
            sambastudio_base_uri="your-SambaStudio-base-URI",
            sambastudio_project_id="your-SambaStudio-project-ID",
            sambastudio_endpoint_id="your-SambaStudio-endpoint-ID",
            sambastudio_api_key="your-SambaStudio-endpoint-API-key,
            streaming=False
            model_kwargs={
                "do_sample": False,
                "max_tokens_to_generate": 1000,
                "temperature": 0.7,
                "top_p": 1.0,
                "repetition_penalty": 1,
                "top_k": 50,
                #"process_prompt": False,
                #"select_expert": "Meta-Llama-3-8B-Instruct"
            },
        )
    """

    sambastudio_base_url: str = ''
    """Base url to use"""

    sambastudio_base_uri: str = ''
    """endpoint base uri"""

    sambastudio_project_id: str = ''
    """Project id on sambastudio for model"""

    sambastudio_endpoint_id: str = ''
    """endpoint id on sambastudio for model"""

    sambastudio_api_key: str = ''
    """sambastudio api key"""

    model_kwargs: Optional[dict] = None
    """Key word arguments to pass to the model."""

    streaming: Optional[bool] = False
    """Streaming flag to get streamed response."""

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Get the identifying parameters."""
        return {**{'model_kwargs': self.model_kwargs}}

    @property
    def _llm_type(self) -> str:
        """Return type of llm."""
        return 'Sambastudio LLM'

    @pre_init
    def validate_environment(cls, values: Dict) -> Dict:
        """Validate that api key and python package exists in environment."""
        values['sambastudio_base_url'] = get_from_dict_or_env(values, 'sambastudio_base_url', 'SAMBASTUDIO_BASE_URL')
        values['sambastudio_base_uri'] = get_from_dict_or_env(
            values,
            'sambastudio_base_uri',
            'SAMBASTUDIO_BASE_URI',
            default='api/predict/generic',
        )
        values['sambastudio_project_id'] = get_from_dict_or_env(
            values, 'sambastudio_project_id', 'SAMBASTUDIO_PROJECT_ID'
        )
        values['sambastudio_endpoint_id'] = get_from_dict_or_env(
            values, 'sambastudio_endpoint_id', 'SAMBASTUDIO_ENDPOINT_ID'
        )
        values['sambastudio_api_key'] = get_from_dict_or_env(values, 'sambastudio_api_key', 'SAMBASTUDIO_API_KEY')
        return values

    def _get_tuning_params(self, stop: Optional[List[str]]) -> str:
        """
        Get the tuning parameters to use when calling the LLM.

        Args:
            stop: Stop words to use when generating. Model output is cut off at the
                first occurrence of any of the stop substrings.

        Returns:
            The tuning parameters as a JSON string.
        """
        _model_kwargs = self.model_kwargs or {}
        _kwarg_stop_sequences = _model_kwargs.get('stop_sequences', [])
        _stop_sequences = stop or _kwarg_stop_sequences
        # if not _kwarg_stop_sequences:
        # _model_kwargs["stop_sequences"] = ",".join(
        #    f'"{x}"' for x in _stop_sequences
        # )
        if 'api/v2/predict/generic' in self.sambastudio_base_uri:
            tuning_params_dict = _model_kwargs
        else:
            tuning_params_dict = {k: {'type': type(v).__name__, 'value': str(v)} for k, v in (_model_kwargs.items())}
        # _model_kwargs["stop_sequences"] = _kwarg_stop_sequences
        tuning_params = json.dumps(tuning_params_dict)
        return tuning_params

    def _handle_nlp_predict(self, sdk: SSEndpointHandler, prompt: Union[List[str], str], tuning_params: str) -> str:
        """
        Perform an NLP prediction using the SambaStudio endpoint handler.

        Args:
            sdk: The SSEndpointHandler to use for the prediction.
            prompt: The prompt to use for the prediction.
            tuning_params: The tuning parameters to use for the prediction.

        Returns:
            The prediction result.

        Raises:
            ValueError: If the prediction fails.
        """
        response = sdk.nlp_predict(
            self.sambastudio_project_id,
            self.sambastudio_endpoint_id,
            self.sambastudio_api_key,
            prompt,
            tuning_params,
        )
        if response['status_code'] != 200:
            optional_detail = response.get('detail')
            if optional_detail:
                raise RuntimeError(
                    f"Sambanova /complete call failed with status code "
                    f"{response['status_code']}.\n Details: {optional_detail}"
                )
            else:
                raise RuntimeError(
                    f"Sambanova /complete call failed with status code "
                    f"{response['status_code']}.\n response {response}"
                )
        if 'api/predict/nlp' in self.sambastudio_base_uri:
            return response['data'][0]['completion']
        elif 'api/v2/predict/generic' in self.sambastudio_base_uri:
            return response['items'][0]['value']['completion']
        elif 'api/predict/generic' in self.sambastudio_base_uri:
            return response['predictions'][0]['completion']
        else:
            raise ValueError(f'handling of endpoint uri: {self.sambastudio_base_uri} not implemented')

    def _handle_completion_requests(self, prompt: Union[List[str], str], stop: Optional[List[str]]) -> str:
        """
        Perform a prediction using the SambaStudio endpoint handler.

        Args:
            prompt: The prompt to use for the prediction.
            stop: stop sequences.

        Returns:
            The prediction result.

        Raises:
            ValueError: If the prediction fails.
        """
        ss_endpoint = SSEndpointHandler(self.sambastudio_base_url, self.sambastudio_base_uri)
        tuning_params = self._get_tuning_params(stop)
        return self._handle_nlp_predict(ss_endpoint, prompt, tuning_params)

    def _handle_nlp_predict_stream(
        self, sdk: SSEndpointHandler, prompt: Union[List[str], str], tuning_params: str
    ) -> Iterator[GenerationChunk]:
        """
        Perform a streaming request to the LLM.

        Args:
            sdk: The SVEndpointHandler to use for the prediction.
            prompt: The prompt to use for the prediction.
            tuning_params: The tuning parameters to use for the prediction.

        Returns:
            An iterator of GenerationChunks.
        """
        for chunk in sdk.nlp_predict_stream(
            self.sambastudio_project_id,
            self.sambastudio_endpoint_id,
            self.sambastudio_api_key,
            prompt,
            tuning_params,
        ):
            if chunk['status_code'] != 200:
                error = chunk.get('error')
                if error:
                    optional_code = error.get('code')
                    optional_details = error.get('details')
                    optional_message = error.get('message')
                    raise ValueError(
                        f"Sambanova /complete call failed with status code "
                        f"{chunk['status_code']}.\n"
                        f"Message: {optional_message}\n"
                        f"Details: {optional_details}\n"
                        f"Code: {optional_code}\n"
                    )
                else:
                    raise RuntimeError(
                        f"Sambanova /complete call failed with status code " f"{chunk['status_code']}." f"{chunk}."
                    )
            if 'api/predict/nlp' in self.sambastudio_base_uri:
                text = json.loads(chunk['data'])['stream_token']
            elif 'api/v2/predict/generic' in self.sambastudio_base_uri:
                text = chunk['result']['items'][0]['value']['stream_token']
            elif 'api/predict/generic' in self.sambastudio_base_uri:
                if len(chunk['result']['responses']) > 0:
                    text = chunk['result']['responses'][0]['stream_token']
                else:
                    text = ''
            else:
                raise ValueError(f'handling of endpoint uri: {self.sambastudio_base_uri}' f'not implemented')
            generated_chunk = GenerationChunk(text=text)
            yield generated_chunk

    def _stream(
        self,
        prompt: Union[List[str], str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[GenerationChunk]:
        """Call out to Sambanova's complete endpoint.

        Args:
            prompt: The prompt to pass into the model.
            stop: Optional list of stop words to use when generating.

        Returns:
            The string generated by the model.
        """
        ss_endpoint = SSEndpointHandler(self.sambastudio_base_url, self.sambastudio_base_uri)
        tuning_params = self._get_tuning_params(stop)
        try:
            if self.streaming:
                for chunk in self._handle_nlp_predict_stream(ss_endpoint, prompt, tuning_params):
                    if run_manager:
                        run_manager.on_llm_new_token(chunk.text)
                    yield chunk
            else:
                return
        except Exception as e:
            # Handle any errors raised by the inference endpoint
            raise ValueError(f'Error raised by the inference endpoint: {e}') from e

    def _handle_stream_request(
        self,
        prompt: Union[List[str], str],
        stop: Optional[List[str]],
        run_manager: Optional[CallbackManagerForLLMRun],
        kwargs: Dict[str, Any],
    ) -> str:
        """
        Perform a streaming request to the LLM.

        Args:
            prompt: The prompt to generate from.
            stop: Stop words to use when generating. Model output is cut off at the
                first occurrence of any of the stop substrings.
            run_manager: Callback manager for the run.
            **kwargs: Additional keyword arguments. directly passed
                to the sambastudio model in API call.

        Returns:
            The model output as a string.
        """
        completion = ''
        for chunk in self._stream(prompt=prompt, stop=stop, run_manager=run_manager, **kwargs):
            completion += chunk.text
        return completion

    def _call(
        self,
        prompt: Union[List[str], str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        """Call out to Sambanova's complete endpoint.

        Args:
            prompt: The prompt to pass into the model.
            stop: Optional list of stop words to use when generating.

        Returns:
            The string generated by the model.
        """
        if stop is not None:
            raise Exception('stop not implemented')
        try:
            if self.streaming:
                return self._handle_stream_request(prompt, stop, run_manager, kwargs)
            return self._handle_completion_requests(prompt, stop)
        except Exception as e:
            # Handle any errors raised by the inference endpoint
            raise ValueError(f'Error raised by the inference endpoint: {e}') from e


class SambaNovaCloud(LLM):
    """
    SambaNova Cloud large language models.

    To use, you should have the environment variables
    ``SAMBANOVA_URL`` set with your SambaNova Cloud URL.
    ``SAMBANOVA_API_KEY`` set with your SambaNova Cloud API Key.

    http://cloud.sambanova.ai/

    Example:
    .. code-block:: python

        SambaNovaCloud(
            sambanova_url = SambaNova cloud endpoint URL,
            sambanova_api_key = set with your SambaNova cloud API key,
            max_tokens = mas number of tokens to generate
            stop_tokens = list of stop tokens
            model = model name
        )
    """

    sambanova_url: str = ''
    """SambaNova Cloud Url"""

    sambanova_api_key: str = ''
    """SambaNova Cloud api key"""

    max_tokens: int = None
    """max tokens to generate"""

    stop_tokens: list = ['<|eot_id|>']
    """Stop tokens"""

    model: str = 'llama3-8b'
    """LLM model expert to use"""

    temperature: float = 0.0
    """model temperature"""

    top_p: float = 0.0
    """model top p"""

    top_k: int = 1
    """model top k"""

    stream_api: bool = True
    """use stream api"""

    stream_options: dict = {'include_usage': True}
    """stream options, include usage to get generation metrics"""

    class Config:
        """Configuration for this pydantic object."""

        extra = Extra.forbid

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Get the identifying parameters."""
        return {
            'model': self.model,
            'max_tokens': self.max_tokens,
            'stop': self.stop_tokens,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'top_k': self.top_k,
        }

    def invoke(
        self,
        input: LanguageModelInput,
        config: Optional[RunnableConfig] = None,
        *,
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> str:
        config = ensure_config(config)

        response = self.generate_prompt(
                    [self._convert_input(input)],
                    stop=stop,
                    callbacks=config.get("callbacks"),
                    tags=config.get("tags"),
                    metadata=config.get("metadata"),
                    run_name=config.get("run_name"),
                    run_id=config.pop("run_id", None),
                    **kwargs,
                )
        run_infos = response.run

        if len(run_infos) > 1:
            raise NotImplementedError('Multiple runs not supported')
        
        run_id = run_infos[0].run_id

        #print('Raw response:', response.run)
        #print('Run ID:', run_id)
        #print(USAGE_TRACKER)
        #if run_id in USAGE_TRACKER:
        #    print('Usage:', USAGE_TRACKER[run_id])
        #return response
        return (
            response
            .generations[0][0]
            .text
        )

    @property
    def _llm_type(self) -> str:
        """Return type of llm."""
        return 'SambaNova Cloud'

    @pre_init
    def validate_environment(cls, values: Dict) -> Dict:
        """Validate that api key and python package exists in environment."""
        values['sambanova_url'] = get_from_dict_or_env(
            values, 'sambanova_url', 'SAMBANOVA_URL', default='https://api.sambanova.ai/v1/chat/completions'
        )
        values['sambanova_api_key'] = get_from_dict_or_env(values, 'sambanova_api_key', 'SAMBANOVA_API_KEY')
        return values

    def _handle_nlp_predict_stream(
        self,
        prompt: Union[List[str], str],
        stop: List[str],
    ) -> Iterator[GenerationChunk]:
        """
        Perform a streaming request to the LLM.

        Args:
            prompt: The prompt to use for the prediction.
            stop: list of stop tokens

        Returns:
            An iterator of GenerationChunks.
        """
        try:
            import sseclient
        except ImportError:
            raise ImportError('could not import sseclient library' 'Please install it with `pip install sseclient-py`.')
        try:
            formatted_prompt = json.loads(prompt)
        except:
            formatted_prompt = [{'role': 'user', 'content': prompt}]

        http_session = requests.Session()
        if not stop:
            stop = self.stop_tokens
        data = {
            'messages': formatted_prompt,
            'max_tokens': self.max_tokens,
            'stop': stop,
            'model': self.model,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'top_k': self.top_k,
            'stream': self.stream_api,
            'stream_options': self.stream_options,
        }
        # Streaming output
        response = http_session.post(
            self.sambanova_url,
            headers={'Authorization': f'Bearer {self.sambanova_api_key}', 'Content-Type': 'application/json'},
            json=data,
            stream=True,
        )

        client = sseclient.SSEClient(response)
        close_conn = False

        if response.status_code != 200:
            raise RuntimeError(
                f'Sambanova /complete call failed with status code ' f'{response.status_code}.' f'{response.text}.'
            )

        for event in client.events():
            if event.event == 'error_event':
                close_conn = True
            #print('Event:', event.data)
            chunk = {
                'event': event.event,
                'data': event.data,
                'status_code': response.status_code,
            }

            if chunk.get('error'):
                raise RuntimeError(
                    f"Sambanova /complete call failed with status code " f"{chunk['status_code']}." f"{chunk}."
                )

            try:
                # check if the response is a final event in that case event data response is '[DONE]'
                #if 'usage' in chunk['data']:
                #    usage = json.loads(chunk['data'])
                #    print('Usage:', usage)
                if chunk['data'] != '[DONE]':
                    data = json.loads(chunk['data'])
                    if data.get('error'):
                        raise RuntimeError(
                            f"Sambanova /complete call failed with status code " f"{chunk['status_code']}." f"{chunk}."
                        )
                    # check if the response is a final response with usage stats (not includes content)
                    if data.get('usage') is None:
                        # check is not "end of text" response
                        if data['choices'][0]['finish_reason'] is None:
                            text = data['choices'][0]['delta']['content']
                            generated_chunk = GenerationChunk(text=text)
                            yield generated_chunk
                    else:
                        #if data['id'] not in USAGE_TRACKER:
                        #    USAGE_TRACKER[data['id']] = []
                        #USAGE_TRACKER[data['id']].append(data['usage'])
                        append_to_usage_tracker(data['usage'])
                        #print(f'Usage for id {data["id"]}:', data['usage'])
            except Exception as e:
                raise Exception(f'Error getting content chunk raw streamed response: {chunk}')

    def _stream(
        self,
        prompt: Union[List[str], str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[GenerationChunk]:
        """Call out to Sambanova's complete endpoint.

        Args:
            prompt: The prompt to pass into the model.
            stop: Optional list of stop words to use when generating.

        Returns:
            The string generated by the model.
        """
        try:
            for chunk in self._handle_nlp_predict_stream(prompt, stop):
                if run_manager:
                    run_manager.on_llm_new_token(chunk.text)
                yield chunk
        except Exception as e:
            # Handle any errors raised by the inference endpoint
            raise ValueError(f'Error raised by the inference endpoint: {e}') from e

    def _handle_stream_request(
        self,
        prompt: Union[List[str], str],
        stop: Optional[List[str]],
        run_manager: Optional[CallbackManagerForLLMRun],
        kwargs: Dict[str, Any],
    ) -> str:
        """
        Perform a streaming request to the LLM.

        Args:
            prompt: The prompt to generate from.
            stop: Stop words to use when generating. Model output is cut off at the
                first occurrence of any of the stop substrings.
            run_manager: Callback manager for the run.
            **kwargs: Additional keyword arguments. directly passed
                to the Sambanova Cloud model in API call.

        Returns:
            The model output as a string.
        """
        completion = ''
        for chunk in self._stream(prompt=prompt, stop=stop, run_manager=run_manager, **kwargs):
            completion += chunk.text
        return completion

    def _call(
        self,
        prompt: Union[List[str], str],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        """Call out to Sambanova's  complete endpoint.

        Args:
            prompt: The prompt to pass into the model.
            stop: Optional list of stop words to use when generating.

        Returns:
            The string generated by the model.
        """
        try:
            return self._handle_stream_request(prompt, stop, run_manager, kwargs)
        except Exception as e:
            # Handle any errors raised by the inference endpoint
            raise ValueError(f'Error raised by the inference endpoint: {e}') from e