import asyncio
import pyaudio
import config

pya = pyaudio.PyAudio()

def list_audio_devices():
    print("\n--- Available Audio Devices ---")
    device_count = pya.get_device_count()
    if device_count == 0:
        print("No audio devices found.")
        return

    for i in range(device_count):
        try:
            device_info = pya.get_device_info_by_index(i)
            if device_info.get('maxInputChannels', 0) > 0:
                print(f"  Device #{i}:")
                print(f"    Name: {device_info.get('name')}")
                print(f"    Max Input Channels: {device_info.get('maxInputChannels')}")
                print(f"    Default Sample Rate: {device_info.get('defaultSampleRate')}")
                print("    ----------")
        except Exception as e:
            print(f"Could not get info for device {i}: {e}")
    print("-----------------------------\n")

async def listen_audio_task(live_session, out_queue, socketio_instance, is_running_flag):
    print("[DEBUG] Entered listen_audio_task")
    audio_stream = await asyncio.to_thread(
        pya.open,
        format=config.FORMAT,
        channels=config.CHANNELS,
        rate=config.SEND_SAMPLE_RATE,
        input=True,
        input_device_index=config.INPUT_DEVICE_INDEX,
        frames_per_buffer=config.CHUNK_SIZE,
    )
    print("Microphone stream opened.")
    while is_running_flag['is_running']:
        try:
            data = await asyncio.to_thread(audio_stream.read, config.CHUNK_SIZE, exception_on_overflow=False)
            if is_running_flag['is_running']:
                await out_queue.put({"data": data, "mime_type": "audio/pcm"})
                socketio_instance.emit('user_text', {'text': '[User speaking...]'})
        except asyncio.CancelledError:
            break
        except IOError as e:
            if e.errno == pyaudio.paInputOverflowed:
                print("Input overflowed. Skipping frame.")
            else:
                print(f"IOError in listen_audio_task: {e}")
                socketio_instance.emit('error', {'message': f'Audio input error: {str(e)}'})
                break
        except Exception as e:
            print(f"Error in listen_audio_task: {e}")
            socketio_instance.emit('error', {'message': f'Audio input error: {str(e)}'})
            break

    audio_stream.stop_stream()
    audio_stream.close()
    print("Microphone stream closed.")

async def play_audio_task(audio_in_queue, socketio_instance, is_running_flag):
    stream = await asyncio.to_thread(
        pya.open,
        format=config.FORMAT,
        channels=config.CHANNELS,
        rate=config.RECEIVE_SAMPLE_RATE,
        output=True,
    )
    print("Audio output stream opened.")
    while is_running_flag['is_running']:
        try:
            bytestream = await asyncio.wait_for(audio_in_queue.get(), timeout=1.0)
            if is_running_flag['is_running']:
                await asyncio.to_thread(stream.write, bytestream)
            audio_in_queue.task_done()
            if audio_in_queue.empty():
                socketio_instance.emit('bot_speech_end')
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in play_audio_task: {e}")
            socketio_instance.emit('error', {'message': f'Audio playback error: {str(e)}'})
            break
    stream.stop_stream()
    stream.close()
    print("Audio output stream closed.")

def terminate_pyaudio():
    print("Terminating PyAudio...")
    pya.terminate()
    print("PyAudio terminated.")