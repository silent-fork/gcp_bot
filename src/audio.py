import asyncio
import io
import pyaudio
import wave

import config

socketio = None
global_session_vars = None

def list_audio_devices():
    p = pyaudio.PyAudio()
    info = p.get_host_api_info_by_index(0)
    numdevices = info.get('deviceCount')
    print("Available audio devices:")
    for i in range(0, numdevices):
        device_info = p.get_device_info_by_host_api_device_index(0, i)
        if (device_info.get('maxInputChannels')) > 0:
            print(f"Input Device id {i} - {device_info.get('name')} - Channels: {device_info.get('maxInputChannels')}")
    p.terminate()

async def listen_audio_task(out_queue, global_session_vars):
    socketio_instance = socketio
    """Continuously listens to the microphone and puts audio data into a queue."""
    p = pyaudio.PyAudio()
    stream = None
    try:
        stream = p.open(
            format=config.FORMAT,
            channels=config.CHANNELS,
            rate=config.SEND_SAMPLE_RATE,
            input=True,
            frames_per_buffer=config.CHUNK_SIZE,
            input_device_index=config.INPUT_DEVICE_INDEX
        )

        while global_session_vars['is_running'] and global_session_vars['mic_is_on']:
            try:
                data = await asyncio.to_thread(stream.read, config.CHUNK_SIZE, exception_on_overflow=False)
                await out_queue.put({"mime_type": f"audio/l16;rate={config.SEND_SAMPLE_RATE}", "data": data})
            except IOError as e:
                if e.errno == pyaudio.paInputOverflowed:
                    print("Input overflowed. Skipping frame.")
                else:
                    raise

    except Exception as e:
        print(f"Error in listen_audio_task: {e}")
        socketio_instance.emit('error', {'message': f'Audio input error: {str(e)}'})
    finally:
        if stream:
            stream.stop_stream()
            stream.close()
        if p:
            p.terminate()
        print("Listen audio task ended")

async def play_audio_task(in_queue):
    """Plays audio from a queue."""
    p = pyaudio.PyAudio()
    stream = None
    buffer = io.BytesIO()
    wf = wave.open(buffer, 'wb')
    wf.setnchannels(config.CHANNELS)
    wf.setsampwidth(p.get_sample_size(config.FORMAT))
    wf.setframerate(config.RECEIVE_SAMPLE_RATE)

    stream = None
    try:
        while True:
            data = await in_queue.get()
            if data is None:
                break
            wf.writeframes(data)
            in_queue.task_done()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Error in play_audio_task: {e}")
    finally:
        wf.close()
        buffer.seek(0)

        if buffer.getbuffer().nbytes > 0:
            try:
                stream = p.open(format=config.FORMAT,
                                channels=config.CHANNELS,
                                rate=config.RECEIVE_SAMPLE_RATE,
                                output=True)
                wf_read = wave.open(buffer, 'rb')
                while len(data := wf_read.readframes(config.CHUNK_SIZE)):
                    stream.write(data)
            except Exception as e:
                print(f"Error playing audio: {e}")
            finally:
                if stream:
                    stream.stop_stream()
                    stream.close()
        if p:
            p.terminate()
        print("Play audio task ended")


def terminate_pyaudio(p_instance):
    if p_instance:
        p_instance.terminate()