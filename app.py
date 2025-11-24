from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename
from transcribe import transcriber
from utils.srt import words_to_srt, words_to_ass
import os
import uuid
import threading
import time
import queue
from datetime import datetime
import ffmpeg
import tempfile
import gc
import psutil
import logging
from collections import OrderedDict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024 * 1024  # Changed to 3GB to safely allow 2GB+ files
app.config['TEMP_FOLDER'] = 'static/temp'
app.config['MAX_JOBS_IN_MEMORY'] = 5  # Keep only recent jobs in memory
app.config['JOB_CLEANUP_HOURS'] = 24  # Clean up jobs older than 24 hours

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_FOLDER'], exist_ok=True)

# Job management system
class JobManager:
    def __init__(self):
        self.jobs = OrderedDict()  # LRU cache for jobs
        self.job_queue = queue.Queue()
        self.currently_processing = None
        self.job_lock = threading.Lock()
        self.processing_thread = None
        self.shutdown_flag = False
        self._first_request_handled = False
        
    def start_processor(self):
        """Start the background job processing thread"""
        if self.processing_thread is None or not self.processing_thread.is_alive():
            self.shutdown_flag = False
            self.processing_thread = threading.Thread(target=self._process_jobs, daemon=True)
            self.processing_thread.start()
            logger.info("Job processor thread started")
    
    def stop_processor(self):
        """Stop the job processor"""
        self.shutdown_flag = True
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=5)
            logger.info("Job processor stopped")
    
    def create_job(self, filename, filepath, output_format='srt', use_vad=True):
        """Create a new job and add to queue"""
        job_id = str(uuid.uuid4())
        
        with self.job_lock:
            # Clean up old jobs if we have too many in memory
            self._cleanup_old_jobs()
            
            self.jobs[job_id] = {
                'id': job_id,
                'filename': filename,
                'filepath': filepath,
                'output_format': output_format,
                'use_vad': use_vad,
                'status': 'waiting',  # waiting, processing, completed, error
                'created_at': datetime.now().isoformat(),
                'started_at': None,
                'completed_at': None,
                'result_path': None,
                'error_message': None,
                'file_size_mb': 0,
                'word_count': 0,
                'language': None
            }
            
            # Add to processing queue
            self.job_queue.put(job_id)
            logger.info(f"Job {job_id} created for file {filename}")
            
        return job_id
    
    def get_job_status(self, job_id):
        """Get current status of a job"""
        with self.job_lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            
            # Return minimal status info for API
            status_info = {
                'job_id': job['id'],
                'status': job['status'],
                'filename': job['filename'],
                'created_at': job['created_at'],
                'started_at': job['started_at'],
                'completed_at': job['completed_at']
            }
            
            # Add result info if completed
            if job['status'] == 'completed':
                status_info.update({
                    'result_path': job['result_path'],
                    'file_size_mb': job['file_size_mb'],
                    'word_count': job['word_count'],
                    'language': job['language'],
                    'download_url': f"/api/download/{job_id}"
                })
            elif job['status'] == 'error':
                status_info['error_message'] = job['error_message']
                
            return status_info
    
    def get_queue_position(self, job_id):
        """Get position in queue for a job"""
        with self.job_lock:
            if job_id not in self.jobs:
                return -1
                
            # Convert queue to list to find position
            queue_list = list(self.job_queue.queue)
            try:
                position = queue_list.index(job_id) + 1
                if self.currently_processing == job_id:
                    return 0  # Currently processing
                return position
            except ValueError:
                # Job might be processing or completed
                if self.currently_processing == job_id:
                    return 0
                return -1  # Not in queue (completed or error)
    
    def _cleanup_old_jobs(self):
        """Clean up jobs older than configured hours"""
        cutoff_time = datetime.now().timestamp() - (app.config['JOB_CLEANUP_HOURS'] * 3600)
        
        jobs_to_remove = []
        for job_id, job in self.jobs.items():
            job_time = datetime.fromisoformat(job['created_at']).timestamp()
            if job_time < cutoff_time:
                jobs_to_remove.append(job_id)
        
        for job_id in jobs_to_remove:
            self._cleanup_job_files(job_id)
            del self.jobs[job_id]
            logger.info(f"Cleaned up old job: {job_id}")
        
        # Also enforce max jobs in memory
        while len(self.jobs) >= app.config['MAX_JOBS_IN_MEMORY']:
            oldest_job_id = next(iter(self.jobs))
            self._cleanup_job_files(oldest_job_id)
            del self.jobs[oldest_job_id]
            logger.info(f"Cleaned up LRU job: {oldest_job_id}")
    
    def _process_jobs(self):
        """Background thread to process jobs one by one"""
        logger.info("Job processor started")
        
        while not self.shutdown_flag:
            try:
                # Get next job with timeout to allow shutdown check
                job_id = self.job_queue.get(timeout=1)
            except queue.Empty:
                continue
                
            try:
                with self.job_lock:
                    if job_id not in self.jobs:
                        continue
                    
                    job = self.jobs[job_id]
                    self.currently_processing = job_id
                    job['status'] = 'processing'
                    job['started_at'] = datetime.now().isoformat()
                
                logger.info(f"Processing job {job_id}: {job['filename']}")
                self._process_single_job(job_id)
                
            except Exception as e:
                logger.error(f"Error processing job {job_id}: {e}")
                with self.job_lock:
                    if job_id in self.jobs:
                        self.jobs[job_id]['status'] = 'error'
                        self.jobs[job_id]['error_message'] = str(e)
            finally:
                with self.job_lock:
                    self.currently_processing = None
                self.job_queue.task_done()
                
        logger.info("Job processor stopped")
    
    def _process_single_job(self, job_id):
        """Process a single transcription job"""
        with self.job_lock:
            job = self.jobs[job_id]
        
        temp_files = []
        try:
            # Get file info
            file_size_mb = os.path.getsize(job['filepath']) / (1024 * 1024)
            
            # Extract audio
            audio_path = self._extract_audio(job['filepath'])
            temp_files.append(audio_path)
            
            # Transcribe using large-v3 model always
            if job['use_vad']:
                result = transcriber.transcribe_large_file(
                    audio_path,
                    language=None,  # Auto-detect
                    chunk_size=200  # 5-minute chunks
                )
            else:
                result = transcriber.transcribe_file_no_vad(
                    audio_path,
                    word_timestamps=True, 
                    use_large_model=True  # Force large model
                )
            
            # Generate captions
            base_filename = job['filename'].rsplit('.', 1)[0]
            
            if job['output_format'] == 'ass':
                caption_content = words_to_ass_advanced(result['words'])
                file_extension = ".ass"
            else:
                caption_content = words_to_srt(result['words'])
                file_extension = ".srt"
            
            # Save result with job ID for unique naming
            result_filename = f"{base_filename}_{job_id}{file_extension}"
            result_path = os.path.join(app.config['UPLOAD_FOLDER'], result_filename)
            
            with open(result_path, 'w', encoding='utf-8') as f:
                f.write(caption_content)
            
            # Update job with results
            with self.job_lock:
                job['status'] = 'completed'
                job['completed_at'] = datetime.now().isoformat()
                job['result_path'] = result_path
                job['file_size_mb'] = round(file_size_mb, 2)
                job['word_count'] = len(result['words'])
                job['language'] = result['language']
            
            logger.info(f"Job {job_id} completed successfully: {job['word_count']} words")
            
        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}")
            with self.job_lock:
                job['status'] = 'error'
                job['error_message'] = str(e)
            raise
        finally:
            # Cleanup temporary files
            self._cleanup_files(temp_files)
            # Force garbage collection
            gc.collect()
    
    def _extract_audio(self, file_path):
        """Extract audio to temporary file"""
        temp_audio = tempfile.NamedTemporaryFile(
            delete=False, 
            suffix='.wav', 
            dir=app.config['TEMP_FOLDER']
        )
        temp_audio.close()
        
        try:
            stream = ffmpeg.input(file_path)
            stream = ffmpeg.output(
                stream, 
                temp_audio.name, 
                acodec="pcm_s16le", 
                ar="16k", 
                ac=1,
                compression_level=12
            )
            ffmpeg.run(stream, overwrite_output=True, quiet=True)
            return temp_audio.name
        except Exception as e:
            # Clean up on error
            if os.path.exists(temp_audio.name):
                os.remove(temp_audio.name)
            raise e
    
    def _cleanup_files(self, file_list):
        """Clean up temporary files"""
        for file_path in file_list:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"Could not remove {file_path}: {e}")
    
    def _cleanup_job_files(self, job_id):
        """Clean up files associated with a job"""
        job = self.jobs.get(job_id)
        if not job:
            return
            
        # Clean up result file
        if job.get('result_path') and os.path.exists(job['result_path']):
            try:
                os.remove(job['result_path'])
                logger.info(f"Cleaned up result file for job {job_id}")
            except Exception as e:
                logger.warning(f"Could not remove result file for job {job_id}: {e}")
        
        # Clean up uploaded file
        if job.get('filepath') and os.path.exists(job['filepath']):
            try:
                os.remove(job['filepath'])
                logger.info(f"Cleaned up uploaded file for job {job_id}")
            except Exception as e:
                logger.warning(f"Could not remove uploaded file for job {job_id}: {e}")

# Initialize job manager
job_manager = JobManager()

@app.before_request
def before_request_handler():
    """Initialize services on first request"""
    if not job_manager._first_request_handled:
        job_manager.start_processor()
        job_manager._first_request_handled = True

@app.teardown_appcontext
def shutdown(exception=None):
    """Cleanup on app shutdown"""
    if exception:
        logger.error(f"App context teardown with exception: {exception}")

# API Routes
@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'system': {
            'memory_available_mb': round(memory.available / (1024 * 1024), 2),
            'memory_percent': memory.percent,
            'disk_free_gb': round(disk.free / (1024 * 1024 * 1024), 2)
        },
        'jobs': {
            'waiting': job_manager.job_queue.qsize(),
            'processing': 1 if job_manager.currently_processing else 0,
            'completed': len([j for j in job_manager.jobs.values() if j['status'] == 'completed']),
            'total_in_memory': len(job_manager.jobs)
        }
    })

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Upload file for transcription - returns job ID immediately"""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    # Validate file size
    file.seek(0, 2)  # Seek to end to get size
    file_size = file.tell()
    file.seek(0)  # Reset seek position
    
    if file_size > app.config['MAX_CONTENT_LENGTH']:
        return jsonify({"error": f"File too large. Max size: {app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024 * 1024)}GB"}), 400
    
    try:
        # Save uploaded file via streaming to avoid memory overflow (critical for 2GB+ files)
        filename = secure_filename(file.filename)
        unique_id = str(uuid.uuid4())
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"{unique_id}_{filename}")

        with open(filepath, 'wb') as f:
            chunk_size = 10 * 1024 * 1024  # 10MB chunks
            while True:
                chunk = file.stream.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
        
        # Get processing parameters
        output_format = request.form.get('format', 'srt')
        use_vad = request.form.get('use_vad', 'true').lower() == 'true'
        
        # Create job
        job_id = job_manager.create_job(filename, filepath, output_format, use_vad)
        
        return jsonify({
            "job_id": job_id,
            "status": "waiting",
            "filename": filename,
            "message": "File uploaded successfully, processing queued",
            "queue_position": job_manager.get_queue_position(job_id),
            "created_at": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Get current status of a job"""
    status = job_manager.get_job_status(job_id)
    
    if not status:
        return jsonify({"error": "Job not found"}), 404
    
    # Add queue position for waiting jobs
    if status['status'] == 'waiting':
        status['queue_position'] = job_manager.get_queue_position(job_id)
    
    return jsonify(status)

@app.route('/api/jobs', methods=['GET'])
def list_jobs():
    """List all current jobs (for monitoring)"""
    jobs_list = []
    
    with job_manager.job_lock:
        for job_id, job in job_manager.jobs.items():
            jobs_list.append({
                'job_id': job_id,
                'filename': job['filename'],
                'status': job['status'],
                'created_at': job['created_at'],
                'started_at': job['started_at'],
                'completed_at': job['completed_at']
            })
    
    return jsonify({
        'total_jobs': len(jobs_list),
        'jobs': jobs_list
    })

@app.route('/api/download/<job_id>', methods=['GET'])
def download_result(job_id):
    """Download completed transcription result"""
    status = job_manager.get_job_status(job_id)
    
    if not status:
        return jsonify({"error": "Job not found"}), 404
    
    if status['status'] != 'completed':
        return jsonify({"error": "Job not completed"}), 400
    
    if not status.get('result_path') or not os.path.exists(status['result_path']):
        return jsonify({"error": "Result file not found"}), 404
    
    # Determine filename for download
    original_name = status['filename'].rsplit('.', 1)[0]
    extension = '.ass' if 'ass' in status.get('result_path', '') else '.srt'
    download_name = f"{original_name}_captions{extension}"
    
    return send_file(
        status['result_path'],
        as_attachment=True,
        download_name=download_name
    )

@app.route('/api/queue/status', methods=['GET'])
def queue_status():
    """Get overall queue status"""
    waiting_count = job_manager.job_queue.qsize()
    processing = job_manager.currently_processing
    
    return jsonify({
        'waiting_jobs': waiting_count,
        'currently_processing': processing,
        'total_tracked_jobs': len(job_manager.jobs)
    })

@app.route('/api/cleanup', methods=['POST'])
def manual_cleanup():
    """Manual cleanup endpoint for admin purposes"""
    try:
        with job_manager.job_lock:
            initial_count = len(job_manager.jobs)
            job_manager._cleanup_old_jobs()
            final_count = len(job_manager.jobs)
            
        return jsonify({
            "message": f"Cleanup completed. Removed {initial_count - final_count} old jobs.",
            "jobs_remaining": final_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "File too large"}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error: {e}")
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}")
    return jsonify({"error": "Internal server error"}), 500

def shutdown_handler():
    """Handle application shutdown"""
    logger.info("Shutting down application...")
    job_manager.stop_processor()
    logger.info("Application shutdown complete")

# Register shutdown handler
import atexit
atexit.register(shutdown_handler)

if __name__ == '__main__':
    # Start job processor immediately for CLI execution
    job_manager.start_processor()
    
    try:
        app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        shutdown_handler()