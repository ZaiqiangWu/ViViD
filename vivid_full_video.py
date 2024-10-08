import argparse
from datetime import datetime
from pathlib import Path

import torch
from diffusers import AutoencoderKL, DDIMScheduler
from omegaconf import OmegaConf
from PIL import Image
from torchvision import transforms
from transformers import CLIPVisionModelWithProjection

from src.models.pose_guider import PoseGuider
from src.models.unet_2d_condition import UNet2DConditionModel
from src.models.unet_3d import UNet3DConditionModel
from src.pipelines.pipeline_pose2vid_long import Pose2VideoPipeline
from src.utils.util import get_fps, read_frames, save_videos_grid
import os

jin_dict={i:"jin_"+str(i).zfill(2) for i in range(16)}
lab_dict={16+i:"lab_"+str(i).zfill(2) for i in range(9)}
video_dict=jin_dict.copy()
video_dict.update(lab_dict)

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",type=str,default="./configs/prompts/upper1.yaml")
    parser.add_argument("-W", type=int, default=384)
    parser.add_argument("-H", type=int, default=512)
    parser.add_argument("-L", type=int, default=24)#24
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=24)

    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cfg", type=float, default=3.5)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--fps", type=int)
    args = parser.parse_args()

    return args


def main(video_id,garment_id):
    args = parse_args()

    config = OmegaConf.load(args.config)

    if config.weight_dtype == "fp16":
        weight_dtype = torch.float16
    else:
        weight_dtype = torch.float32

    vae = AutoencoderKL.from_pretrained(
        config.pretrained_vae_path,
    ).to("cuda", dtype=weight_dtype)

    reference_unet = UNet2DConditionModel.from_pretrained_2d(
        config.pretrained_base_model_path,
        subfolder="unet",
        unet_additional_kwargs={
            "in_channels": 5,
        }
    ).to(dtype=weight_dtype, device="cuda")

    inference_config_path = config.inference_config
    infer_config = OmegaConf.load(inference_config_path)
    denoising_unet = UNet3DConditionModel.from_pretrained_2d(
        config.pretrained_base_model_path,
        config.motion_module_path,
        subfolder="unet",
        unet_additional_kwargs=infer_config.unet_additional_kwargs,
    ).to(dtype=weight_dtype, device="cuda")

    pose_guider = PoseGuider(320, block_out_channels=(16, 32, 96, 256)).to(
        dtype=weight_dtype, device="cuda"
    )


    image_enc = CLIPVisionModelWithProjection.from_pretrained(
        config.image_encoder_path
    ).to(dtype=weight_dtype, device="cuda")

    sched_kwargs = OmegaConf.to_container(infer_config.noise_scheduler_kwargs)
    scheduler = DDIMScheduler(**sched_kwargs)

    seed = config.get("seed",args.seed)
    generator = torch.manual_seed(seed)

    width, height = args.W, args.H
    clip_length = config.get("L",args.L)
    print("clip length", clip_length)
    steps = args.steps
    guidance_scale = args.cfg

    # load pretrained weights
    denoising_unet.load_state_dict(
        torch.load(config.denoising_unet_path, map_location="cpu"),
        strict=False,
    )
    reference_unet.load_state_dict(
        torch.load(config.reference_unet_path, map_location="cpu"),
    )

    pose_guider.load_state_dict(
        torch.load(config.pose_guider_path, map_location="cpu"),
    )

    pipe = Pose2VideoPipeline(
        vae=vae,
        image_encoder=image_enc,
        reference_unet=reference_unet,
        denoising_unet=denoising_unet,
        pose_guider=pose_guider,
        scheduler=scheduler,
    )
    pipe = pipe.to("cuda", dtype=weight_dtype)

    date_str = datetime.now().strftime("%Y%m%d")
    time_str = datetime.now().strftime("%H%M")
    save_dir_name = f"{time_str}--seed_{seed}-{args.W}x{args.H}"

    save_dir = Path(f"output/{date_str}/{save_dir_name}")
    save_dir.mkdir(exist_ok=True, parents=True)

    model_video_path = "data/videos/"+video_dict[video_id]+".mp4"#config.model_video_paths
    cloth_image_path = "data/cloth/"+video_dict[garment_id]+".jpg"#config.cloth_image_paths

    transform = transforms.Compose(
        [transforms.Resize((height, width)), transforms.ToTensor()]
    )


    #for model_image_path in model_video_paths: # for each video
    model_image_path = model_video_path
    src_fps = get_fps(model_image_path)

    model_name = Path(model_image_path).stem
    agnostic_path=model_image_path.replace("videos","agnostic")
    agn_mask_path=model_image_path.replace("videos","agnostic_mask")
    densepose_path=model_image_path.replace("videos","densepose")

    video_images=read_frames(model_image_path)
    agnostic_images = read_frames(agnostic_path)
    agn_mask_images=read_frames(agn_mask_path)
    pose_images=read_frames(densepose_path)
    n_frames = len(video_images)
    print("Total frames", n_frames)
    batch_size=32
    start_id = 0
    end_id = start_id + batch_size
    result_video_list = []

    while start_id < n_frames:
        if end_id > n_frames:
            end_id = n_frames
        clip_length=end_id-start_id
        print("Start id: ",start_id)
        print("End id: ", end_id)
        video_tensor_list = []
        for vid_image_pil in video_images[start_id:end_id]:
            video_tensor_list.append(transform(vid_image_pil))

        video_tensor = torch.stack(video_tensor_list, dim=0)  # (f, c, h, w)
        video_tensor = video_tensor.transpose(0, 1)

        agnostic_list = []
        for agnostic_image_pil in agnostic_images[start_id:end_id]:
            agnostic_list.append(agnostic_image_pil)

        agn_mask_list = []
        for agn_mask_image_pil in agn_mask_images[start_id:end_id]:
            agn_mask_list.append(agn_mask_image_pil)

        pose_list = []
        for pose_image_pil in pose_images[start_id:end_id]:
            pose_list.append(pose_image_pil)

        video_tensor = video_tensor.unsqueeze(0)

        # for cloth_image_path in cloth_image_paths:
        cloth_name = Path(cloth_image_path).stem
        cloth_image_pil = Image.open(cloth_image_path).convert("RGB")

        cloth_mask_path = cloth_image_path.replace("cloth", "cloth_mask")
        cloth_mask_pil = Image.open(cloth_mask_path).convert("RGB")

        pipeline_output = pipe(
            agnostic_list,
            agn_mask_list,
            cloth_image_pil,
            cloth_mask_pil,
            pose_list,
            width,
            height,
            clip_length,
            steps,
            guidance_scale,
            generator=generator,
        )
        video = pipeline_output.videos
        result_video_list.append(video)
        start_id+=batch_size
        end_id=start_id+batch_size
        print(video.shape)#[1, 3, 8, 512, 384]





    video = torch.cat(result_video_list,dim=2)#torch.cat([video_tensor,video], dim=0)
    print(video.shape)
    target_dir='./vivid_results'
    os.makedirs(target_dir,exist_ok=True)
    v_path=os.path.join(target_dir,str(video_id).zfill(2)+"_"+str(garment_id).zfill(2)+".mp4")
    save_videos_grid(
        video,
        v_path,
        n_rows=1,
        fps=src_fps if args.fps is None else args.fps,
    )


if __name__ == "__main__":
    main(17,5)
    #for i in range(25):
    #    main(i,i)
