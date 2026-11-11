(() => {
    const panel = document.getElementById('window-input');
    const button = document.getElementById('window-share');
    const message = document.getElementById('window-input-status');
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', {alpha: false});
    let stream = null, video = null, token = null, timer = null, lastTime = -1;

    function post(path, captureToken, image) {
        const headers = {'Content-Type': image ? 'image/jpeg' : 'application/json'};
        if (captureToken) headers['X-Capture-Token'] = captureToken;
        return fetch('/api/capture/' + path, {method: 'POST', headers, body: image || '{}'});
    }

    function stop(note) {
        clearTimeout(timer);
        timer = null;
        const previous = token;
        token = null;
        if (stream) stream.getTracks().forEach(track => track.stop());
        if (video) video.srcObject = null;
        stream = video = null;
        lastTime = -1;
        button.textContent = '选择投屏窗口';
        if (note) message.textContent = note;
        if (previous) post('end', previous).catch(() => {});
    }

    async function upload(lease) {
        if (token !== lease || !video) return;
        try {
            const track = stream.getVideoTracks()[0];
            if (!track.muted && video.readyState >= 2 && video.videoWidth && video.videoHeight && video.currentTime !== lastTime) {
                lastTime = video.currentTime;
                const ratio = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
                const width = Math.round(video.videoWidth * ratio), height = Math.round(video.videoHeight * ratio);
                if (canvas.width !== width || canvas.height !== height) {
                    canvas.width = width;
                    canvas.height = height;
                }
                ctx.drawImage(video, 0, 0, width, height);
                const frame = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .88));
                if (token !== lease) return;
                if (!frame) throw Error('无法编码投屏画面');
                const response = await post('frame', lease, frame);
                if (!response.ok) throw Error('窗口取屏已结束，请重新选择');
            }
        } catch (error) {
            if (token === lease) stop(error.message);
        } finally {
            if (token === lease) timer = setTimeout(() => upload(lease), 125);
        }
    }

    button.addEventListener('click', async () => {
        if (stream) { stop('窗口取屏已停止'); return; }
        if (!navigator.mediaDevices?.getDisplayMedia) {
            message.textContent = '请在 Edge 或 Chrome 中打开此本地页面';
            return;
        }
        button.disabled = true;
        try {
            stream = await navigator.mediaDevices.getDisplayMedia({
                video: {displaySurface: 'window', frameRate: {ideal: 10, max: 15}},
                audio: false, monitorTypeSurfaces: 'exclude', selfBrowserSurface: 'exclude',
            });
            const track = stream.getVideoTracks()[0];
            if (!['window', 'browser'].includes(track.getSettings().displaySurface)) {
                throw Error('请选择投屏窗口或标签页');
            }
            video = document.createElement('video');
            video.muted = video.playsInline = true;
            video.srcObject = stream;
            await video.play();
            const response = await post('start');
            if (!response.ok) throw Error('本地取屏服务未就绪');
            token = (await response.json()).token;
            if (track.readyState !== 'live') throw Error('窗口共享已结束');
            track.addEventListener('ended', () => stop('窗口共享已结束'), {once: true});
            button.textContent = '停止窗口取屏';
            message.textContent = '正在读取已选窗口，请保持投屏窗口打开';
            upload(token);
        } catch (error) {
            stop(error.name === 'NotAllowedError' ? '未共享窗口，可以重新选择' : error.message);
        } finally {
            button.disabled = false;
        }
    });
    window.addEventListener('pagehide', () => stop());
    window.updateCapturePanel = state => { panel.hidden = !state.browser_capture; };
})();
